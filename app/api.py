"""화면(JS)에서 window.pywebview.api.* 로 부르는 함수들.

오래 걸리는 작업은 스레드로 돌리고, 진행 상황은 window.onAppEvent(...)로 화면에 보낸다.
"""
import json
import os
import platform
import subprocess
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor

import webview

from . import APP_NAME, MAKER, VERSION, config, excel, ocr, openrouter
from .prompts import REFINE_FORMAT, TRANSFORM_FORMAT, example_rule, length_rule
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
        return {"name": APP_NAME, "version": VERSION, "maker": MAKER}

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

    # ---------- 1단계 ----------
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
        return {"ok": True, "path": path, "name": os.path.basename(path), "pages": pages}

    def start_stage1(self, path, pages_per_student):
        cfg = config.load()
        missing = [
            label
            for key, label in (
                ("clova_url", "CLOVA Invoke URL"),
                ("clova_secret", "CLOVA Secret Key"),
                ("openrouter_key", "OpenRouter API 키"),
                ("refine_model", "정제 모델"),
            )
            if not str(cfg.get(key, "")).strip()
        ]
        if missing:
            return {"ok": False, "error": "설정에서 먼저 입력해 주세요: " + ", ".join(missing)}
        return self._run_bg(self._stage1, path, max(1, int(pages_per_student)), cfg)

    def _stage1(self, path, per, cfg):
        def ocr_progress(n, total_chunks, total_pages):
            if n == 0:
                self._step("split", "done", text=f"{total_pages}페이지 → {total_chunks}구간")
            self._step("ocr", "active", done=n, total=total_chunks)

        self._step("split", "active")
        pages = ocr.ocr_pdf(
            path,
            cfg["clova_url"].strip(),
            cfg["clova_secret"].strip(),
            cfg["ocr_lang"],
            ocr_progress,
            self._stop.is_set,
        )
        chunks = -(-len(pages) // ocr.MAX_PAGES_PER_CALL)
        self._step("ocr", "done", done=chunks, total=chunks)
        groups = [pages[i : i + per] for i in range(0, len(pages), per)]

        def page_range(i, g):
            first = i * per + 1
            return str(first) if len(g) == 1 else f"{first}-{first + len(g) - 1}"

        students = [
            {"index": i, "pages": page_range(i, g), "raw": "\n\n".join(g).strip()}
            for i, g in enumerate(groups)
        ]

        mode = cfg.get("refine_mode", "B")
        system = (cfg.get("refine_prompts") or {}).get(mode, "") + REFINE_FORMAT
        key, model = cfg["openrouter_key"].strip(), cfg["refine_model"]
        done = [0]
        cost = [0.0]
        lock = threading.Lock()

        def refine(st):
            out = {"index": st["index"], "pages": st["pages"], "student_id": "", "name": "", "original": st["raw"], "error": ""}
            if self._stop.is_set():
                out["error"] = "중지되어 정제하지 않았습니다(OCR 원문)."
            elif not st["raw"]:
                out["error"] = "인식된 글자가 없습니다."
            else:
                try:
                    text, c = openrouter.chat(key, model, system, st["raw"])
                    data = openrouter.parse_json_object(text)
                    out["student_id"] = str(data.get("student_id") or "").strip()
                    out["name"] = str(data.get("name") or "").strip()
                    out["original"] = str(data.get("text") or "").strip()
                    with lock:
                        cost[0] += c
                except openrouter.LlmError as e:
                    out["error"] = str(e)
            with lock:
                done[0] += 1
                self._step("refine", "active", done=done[0], total=len(students))
            return out

        self._step("refine", "active", done=0, total=len(students))
        with ThreadPoolExecutor(LLM_WORKERS) as ex:
            results = list(ex.map(refine, students))
        self._step("refine", "done", done=len(students), total=len(students))
        self._emit(
            {
                "type": "stage1_done",
                "students": results,
                "stopped": self._stop.is_set(),
                "cost": cost[0],
                "balance": openrouter.balance(key),
            }
        )

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
