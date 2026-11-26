"""화면(JS)에서 window.pywebview.api.* 로 부르는 함수들.

오래 걸리는 작업은 스레드로 돌리고, 진행 상황은 window.onAppEvent(...)로 화면에 보낸다.
"""
import json
import os
import re
import platform
import subprocess
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor

import webview

from . import APP_NAME, MAKER, PAGES_URL, TAGLINE, VERSION, config, excel, ocr, ocr_layout, openrouter, scan, updater
from .prompts import REFINE_FORMAT, REFINE_FORMAT_IDS, TRANSFORM_FORMAT, example_rule, length_rule
from .textutil import count_bytes

LLM_WORKERS = 4


def _read_clipboard() -> str:
    system = platform.system()
    if system == "Darwin":
        return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    if system == "Windows":
        import ctypes
        from ctypes import wintypes

        CF_UNICODETEXT = 13
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(ptr) if ptr else ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    return ""


class Api:
    def __init__(self):
        self._window = None
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self._scan = None  # {"path", "pages": [CLOVA 쪽 결과 또는 None], "modes": {쪽: 2단 나누기 방식}}
        self._images = {}
        self._update_info = None
        self._update_ready = ""

    def attach(self, window):
        self._window = window

    # ---------- 공통 ----------
    def _emit(self, payload: dict):
        if self._window:
            self._window.evaluate_js(f"window.onAppEvent({json.dumps(payload, ensure_ascii=False)})")

    def _step(self, key, state, **extra):
        self._emit({"type": "step", "key": key, "state": state, **extra})

    def _run_bg(self, target, *args):
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "다른 작업이 진행 중입니다."}
        self._stop.clear()

        def wrapper():
            try:
                target(*args)
            except ocr.Stopped:
                self._emit({"type": "stopped"})
            except Exception as e:  # 화면에 알리고 끝낸다
                self._emit({"type": "error", "message": str(e)})
            finally:
                self._busy.release()
                self._emit({"type": "idle"})

        threading.Thread(target=wrapper, daemon=True).start()
        return {"ok": True}

    def stop(self):
        self._stop.set()
        return {"ok": True}

    def app_info(self):
        return {"name": APP_NAME, "version": VERSION, "maker": MAKER, "tagline": TAGLINE, "pages_url": PAGES_URL}

    def guide_html(self):
        from .paths import web_dir

        return (web_dir() / "guide.html").read_text(encoding="utf-8")

    def load_config(self):
        cfg = config.load()
        cfg["config_path"] = str(config.config_path())
        return cfg

    def save_config(self, cfg):
        config.save(cfg)
        return {"ok": True}

    def reset_keys(self):
        config.reset_keys()
        return {"ok": True}

    def open_url(self, url):
        if url.startswith("https://"):
            webbrowser.open(url)
        return {"ok": True}

    def read_clipboard(self):
        try:
            return {"ok": True, "text": _read_clipboard().strip()}
        except Exception:
            return {"ok": False, "error": "클립보드를 읽지 못했습니다."}

    def model_info(self, model_id):
        try:
            return {"ok": True, **openrouter.model_info(model_id)}
        except openrouter.LlmError as e:
            return {"ok": False, "error": str(e)}

    def balance(self):
        return {"ok": True, "balance": openrouter.balance(config.load()["openrouter_key"])}

    def count_bytes(self, text):
        return count_bytes(text)

    # ---------- 1단계: PDF 선택 ----------
    def pick_pdf(self):
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("PDF 파일 (*.pdf)",)
        )
        if not result:
            return {"ok": False}
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            from pypdf import PdfReader

            pages = len(PdfReader(path).pages)
        except Exception:
            return {"ok": False, "error": "PDF를 열지 못했습니다."}
        if not pages:
            return {"ok": False, "error": "PDF에 페이지가 없습니다."}
        self._scan = {"path": path, "pages": [None] * pages, "modes": {}}
        self._images = {}
        return {"ok": True, "path": path, "name": os.path.basename(path), "pages": pages}

    def _missing_keys(self, cfg):
        return [
            label
            for key, label in (
                ("clova_url", "CLOVA Invoke URL"),
                ("clova_secret", "CLOVA Secret Key"),
                ("openrouter_key", "OpenRouter API 키"),
                ("refine_model", "정제 모델"),
            )
            if not str(cfg.get(key, "")).strip()
        ]

    # ---------- 1단계: OCR ----------
    def start_ocr(self, only_missing=False):
        """CLOVA OCR. only_missing이면 아직 못 읽었거나 실패한 쪽만 다시 읽는다."""
        if not self._scan:
            return {"ok": False, "error": "먼저 PDF를 선택해 주세요."}
        cfg = config.load()
        missing = self._missing_keys(cfg)
        if missing:
            return {"ok": False, "error": "설정에서 먼저 입력해 주세요: " + ", ".join(missing)}
        pages = None
        if only_missing:
            pages = [i for i, p in enumerate(self._scan["pages"]) if not p or p.get("fail")]
            if not pages:
                return {"ok": False, "error": "다시 읽을 쪽이 없습니다."}
        else:
            self._scan["pages"] = [None] * len(self._scan["pages"])
            self._scan["modes"] = {}
        return self._run_bg(self._ocr, self._scan, pages, cfg)

    def _ocr(self, sc, pages, cfg):
        def progress(n, total_chunks, total_pages):
            if n == 0:
                count = len(pages) if pages is not None else total_pages
                self._step("split", "done", text=f"{count}페이지 → {total_chunks}구간")
            self._step("ocr", "active", done=n, total=total_chunks)

        def on_chunk(got):
            for i, page in got.items():
                sc["pages"][i] = page

        self._step("split", "active")
        stopped = False
        try:
            ocr.ocr_pdf_pages(
                sc["path"], cfg["clova_url"].strip(), cfg["clova_secret"].strip(), cfg["ocr_lang"],
                progress, self._stop.is_set, pages, on_chunk,
            )
            self._step("ocr", "done", text="완료")
        except ocr.Stopped:
            stopped = True
        self._emit({"type": "ocr_done", "stopped": stopped, "pages": self.scan_pages()})

    def _mode_of(self, i):
        return self._scan["modes"].get(i) or config.load().get("layout_mode", "auto")

    def _arranged(self, i):
        page = self._scan["pages"][i]
        if not page:
            return None
        return ocr_layout.arrange(page, self._mode_of(i))

    def _page_text(self, i):
        page = self._arranged(i)
        if not page or page.get("fail"):
            return ""
        return ocr.page_text(page)

    def scan_pages(self):
        """쪽마다 요약: 읽었는지, 실패 이유, 글자 수, 2단 여부, 기울기 경고."""
        out = []
        if not self._scan:
            return out
        for i, raw in enumerate(self._scan["pages"]):
            if not raw:
                out.append({"index": i, "read": False})
                continue
            page = self._arranged(i)
            lay = page.get("layout") or {}
            out.append({
                "index": i, "read": True, "fail": page.get("fail") or "",
                "chars": len(ocr.page_text(page)) if not page.get("fail") else 0,
                "cols": lay.get("cols", 1), "warn": bool(lay.get("warn")),
            })
        return out

    def page_detail(self, i):
        """원본 보기: 그 쪽의 OCR 글자와 배치 정보."""
        i = int(i)
        if not self._scan or not 0 <= i < len(self._scan["pages"]):
            return {"ok": False, "error": "없는 쪽입니다."}
        page = self._arranged(i)
        if not page:
            return {"ok": True, "index": i, "read": False, "text": "", "mode": self._scan["modes"].get(i, "")}
        lay = page.get("layout") or {}
        return {
            "ok": True, "index": i, "read": True, "fail": page.get("fail") or "",
            "text": ocr.page_text(page), "mode": self._scan["modes"].get(i, ""),
            "cols": lay.get("cols", 1), "warn": bool(lay.get("warn")), "deg": lay.get("measured", 0),
        }

    def set_page_mode(self, i, mode):
        """한 쪽만 2단 나누기 방식을 바꾼다. 빈 값이면 설정을 따른다."""
        i = int(i)
        if mode in ocr_layout.MODES:
            self._scan["modes"][i] = mode
        else:
            self._scan["modes"].pop(i, None)
        return self.page_detail(i)

    def page_image(self, i, width=900):
        """원본 쪽 그림(PNG data URL). 이 PC 안에서만 그린다."""
        if not self._scan:
            return {"ok": False, "error": "PDF가 없습니다."}
        key = (int(i), int(width))
        if key not in self._images:
            try:
                self._images[key] = scan.render_page(self._scan["path"], key[0], key[1])
            except Exception as e:
                return {"ok": False, "error": str(e) if isinstance(e, FileNotFoundError) else "그림을 그리지 못했습니다."}
        return {"ok": True, "src": self._images[key]}

    def default_groups(self, per):
        n = len(self._scan["pages"]) if self._scan else 0
        return [g for g in scan.default_groups(n, per)]

    def _ids_of(self, group):
        """학생 한 명(쪽 목록)의 학번·이름을 규칙으로(AI 없이). 첫 쪽부터 보고, 빈 값은 다음 쪽에서 채운다."""
        no = name = ""
        for p in group:
            n2, m2 = scan.extract_ids(self._page_text(p), [])
            no, name = no or n2, name or m2
            if no and name:
                break
        return no, name

    def preview_groups(self, groups):
        """묶기 확인: 학생마다 규칙으로 찾은 학번·이름."""
        out = []
        for g in groups:
            no, name = self._ids_of(g)
            out.append({"pages": g, "student_id": no, "name": name})
        return out

    # ---------- 1단계: 정제 ----------
    def start_refine(self, groups):
        """groups: 학생마다 쪽 번호 목록(0부터)."""
        if not self._scan:
            return {"ok": False, "error": "먼저 PDF를 선택해 주세요."}
        cfg = config.load()
        missing = [x for x in self._missing_keys(cfg) if not x.startswith("CLOVA")]
        if missing:
            return {"ok": False, "error": "설정에서 먼저 입력해 주세요: " + ", ".join(missing)}
        groups = [[int(p) for p in g] for g in groups or [] if g]
        if not groups:
            return {"ok": False, "error": "정제할 학생이 없습니다."}
        return self._run_bg(self._refine, groups, cfg)

    def _refine(self, groups, cfg):
        def page_label(g):
            nums = [p + 1 for p in g]
            if nums == list(range(nums[0], nums[-1] + 1)):
                return str(nums[0]) if len(nums) == 1 else f"{nums[0]}-{nums[-1]}"
            return ",".join(str(n) for n in nums)

        mode = cfg.get("refine_mode", "B")
        prompt = (cfg.get("refine_prompts") or {}).get(mode, "")
        key, model = cfg["openrouter_key"].strip(), cfg["refine_model"]
        done = [0]
        cost = [0.0]
        lock = threading.Lock()

        def refine(item):
            i, g = item
            texts = [self._page_text(p) for p in g]
            unread = [p + 1 for p in g if not self._scan["pages"][p] or self._scan["pages"][p].get("fail")]
            no, name = self._ids_of(g)
            content = scan.strip_pages(texts, no, name, [])
            out = {"index": i, "pages": page_label(g), "page_list": g, "student_id": no, "name": name,
                   "id_by": "rule" if no and name else ("ai" if content else ""), "original": content, "error": "",
                   "warn": f"읽지 못한 쪽이 있습니다: p.{', '.join(map(str, unread))}" if unread else ""}
            if self._stop.is_set():
                out["error"] = "중지되어 정제하지 않았습니다(OCR 원문)."
            elif not content:
                out["error"] = "인식된 글자가 없습니다."
            else:
                try:
                    if no and name:
                        # 학번·이름을 규칙으로 찾았으면 인적사항 줄을 지운 본문만 보낸다
                        text, c = openrouter.chat(key, model, prompt + REFINE_FORMAT, content)
                    else:
                        # 못 찾은 값만 AI가 찾게: 첫 쪽 머리글 몇 줄을 함께 보낸다
                        head = "\n".join(l for l in (texts[0] if texts else "").splitlines() if l.strip())
                        head = "\n".join(head.splitlines()[: scan.HEAD_LINES])
                        user = f"<머리글>\n{head}\n</머리글>\n\n<본문>\n{content}\n</본문>"
                        text, c = openrouter.chat(key, model, prompt + REFINE_FORMAT_IDS, user)
                    data = openrouter.parse_json_object(text)
                    out["student_id"] = no or re.sub(r"\D", "", str(data.get("student_id") or ""))
                    out["name"] = name or str(data.get("name") or "").strip()
                    out["original"] = str(data.get("text") or "").strip() or content
                    with lock:
                        cost[0] += c
                except openrouter.LlmError as e:
                    out["error"] = str(e)
            with lock:
                done[0] += 1
                self._step("refine", "active", done=done[0], total=len(groups))
            return out

        self._step("refine", "active", done=0, total=len(groups))
        with ThreadPoolExecutor(LLM_WORKERS) as ex:
            results = list(ex.map(refine, enumerate(groups)))
        self._step("refine", "done", done=len(groups), total=len(groups))
        self._emit(
            {
                "type": "stage1_done",
                "students": results,
                "stopped": self._stop.is_set(),
                "cost": cost[0],
                "balance": openrouter.balance(key),
            }
        )

    # ---------- 업데이트 ----------
    def check_update(self):
        """화면이 준비되면 부른다: 남은 업데이트 파일 정리, 새 버전 확인(뒤에서)."""
        def run():
            updater.cleanup_leftovers()
            info = updater.check()
            if info.get("ok") and info.get("available"):
                self._update_info = info
                self._emit({"type": "update_available", **{k: info.get(k) for k in ("version", "notes", "can_apply")}})
            else:
                self._emit({"type": "update_none", "latest": info.get("latest", ""), "error": info.get("error", "")})

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def update_apply(self):
        info = self._update_info
        if not info:
            return {"ok": False, "error": "새 버전 정보가 없습니다. 앱을 다시 켜 주세요."}
        if not updater.can_apply():
            return {"ok": False, "error": "Windows exe에서만 자동 업데이트할 수 있습니다."}
        if not info.get("sha256"):
            return {"ok": False, "error": "이 릴리스에는 검증값(SHA-256)이 없어 자동 업데이트하지 않습니다. 내려받기 페이지에서 직접 받아 주세요."}
        if self._update_ready:
            return {"ok": False, "error": f"이미 v{self._update_ready}로 바꿔 두었습니다. 다시 시작해 주세요."}
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "작업이 끝난 뒤에 업데이트해 주세요."}

        def run():
            try:
                path = updater.download(info, lambda d, t: self._emit({"type": "update_progress", "done": d, "total": t}))
                updater.swap_only(path)  # exe만 바꿔 둔다. 다시 시작은 사용자가 누를 때
                self._update_ready = info.get("version", "")
                self._emit({"type": "update_ready", "version": self._update_ready})
            except Exception as e:
                updater.log(f"업데이트 실패: {e}")
                self._emit({"type": "update_error", "message": str(e)})
            finally:
                self._busy.release()

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def update_restart(self):
        """바꿔 둔 새 exe를 띄우고 이 창을 닫는다."""
        if not self._update_ready:
            return {"ok": False, "error": "바꿔 둔 새 버전이 없습니다."}
        exe = updater.current_exe()
        try:
            subprocess.Popen([str(exe), "--after-update"], cwd=str(exe.parent), env=updater._clean_env(),
                             creationflags=0x00000008 | 0x00000200, close_fds=True)
        except OSError as e:
            updater.log(f"새 exe 실행 실패: {e}")
            return {"ok": False, "error": f"새 버전을 실행하지 못했습니다. {exe.name}을(를) 직접 실행해 주세요."}
        updater.log("다시 시작: 새 버전 실행")
        threading.Timer(0.3, self._window.destroy).start()
        return {"ok": True}

    # ---------- 2단계 ----------
    def start_transform(self, items, prompt, target_bytes, example=""):
        """items: [{index, original}]"""
        cfg = config.load()
        if not cfg["openrouter_key"].strip():
            return {"ok": False, "error": "설정에서 OpenRouter API 키를 입력해 주세요."}
        if not str(cfg.get("transform_model", "")).strip():
            return {"ok": False, "error": "설정에서 변환 모델을 입력해 주세요."}
        if not (prompt or "").strip():
            return {"ok": False, "error": "변환 지침(프롬프트)을 입력해 주세요."}
        if not items:
            return {"ok": False, "error": "변환할 학생을 체크해 주세요."}
        return self._run_bg(self._transform, items, prompt, int(target_bytes or 0), example or "", cfg)

    def _transform(self, items, prompt, target_bytes, example, cfg):
        system = prompt.strip() + example_rule(example) + length_rule(target_bytes) + TRANSFORM_FORMAT
        key, model = cfg["openrouter_key"].strip(), cfg["transform_model"]
        done = [0]
        cost = [0.0]
        lock = threading.Lock()

        def run(item):
            if self._stop.is_set():
                with lock:
                    done[0] += 1
                    self._emit({"type": "transform_result", "index": item["index"], "skipped": True})
                return
            try:
                if not (item.get("original") or "").strip():
                    raise openrouter.LlmError("원본이 비어 있습니다.")
                text, c = openrouter.chat(key, model, system, item["original"])
                with lock:
                    cost[0] += c
                payload = {"type": "transform_result", "index": item["index"], "text": text}
            except openrouter.LlmError as e:
                payload = {"type": "transform_result", "index": item["index"], "error": str(e)}
            with lock:
                done[0] += 1
                self._emit(payload)
                self._step("transform", "active", done=done[0], total=len(items))

        self._step("transform", "active", done=0, total=len(items))
        with ThreadPoolExecutor(LLM_WORKERS) as ex:
            list(ex.map(run, items))
        self._step("transform", "done", done=len(items), total=len(items))
        self._emit(
            {
                "type": "transform_done",
                "stopped": self._stop.is_set(),
                "cost": cost[0],
                "balance": openrouter.balance(key),
            }
        )

    # ---------- 저장 ----------
    def save_xlsx(self, rows, include_transform, default_name):
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=default_name or "악필교정기_결과.xlsx",
            file_types=("Excel 파일 (*.xlsx)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            excel.write_xlsx(path, rows, bool(include_transform))
        except PermissionError:
            return {"ok": False, "error": "저장하지 못했습니다. 같은 파일이 Excel에서 열려 있으면 닫고 다시 시도해 주세요."}
        except OSError as e:
            return {"ok": False, "error": f"저장하지 못했습니다: {e}"}
        return {"ok": True, "path": path}
