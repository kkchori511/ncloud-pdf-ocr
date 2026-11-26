"""스캔 한 묶음(고른 PDF 하나)의 상태: CLOVA·Qwen 결과, 단 나누기·읽을 단, 하이브리드 검토.

생기부착즙기 inbox.py의 scan_* 함수를 DB 없이 메모리로 옮긴 것이다. 앱을 끄면 사라진다.
- 읽는 방식(mode): clova(CLOVA만) / ai(Qwen만) / hybrid(둘 다 읽고 다른 곳을 사람이 고름)
- CLOVA 결과는 원래 응답(낱말 좌표 포함)을 두고, 단 나누기·읽을 단에 맞춰 그때그때 배치한다(다시 부르지 않음).
- Qwen 결과는 보낸 그림 범위(가림 비율, 읽을 단)에 묶인다. 읽을 단을 바꾸면 그 쪽 Qwen 결과는 지운다.
"""
import threading

from . import ocr, ocr_compare, ocr_layout, ocr_vision, scan

MODES = ("clova", "ai", "hybrid")


class Session:
    def __init__(self, path, n):
        self.path = path
        self.n = int(n)
        self.lock = threading.RLock()
        self.mode = "clova"
        self.cols = "auto"  # 전체 단 나누기
        self.side = "both"  # 전체 읽을 단
        self.page_opts = {}  # 쪽: {"cols", "side"} (그 쪽만 따로)
        self.clova = [None] * self.n  # ocr.image_lines 결과(낱말 좌표 포함, 배치 전)
        self.ai = {"model": None, "mask": None, "pages": [None] * self.n, "cost": 0.0}
        self.review = {}  # 쪽: {"d": {차이 id: {"pick", "text"}}, "page": None|"clova"|"ai"|"edit", "text", "s": 제안, "figs"}
        self.fix_cost = 0.0

    # ---------- 읽는 방식 ----------
    @property
    def need_clova(self):
        return self.mode in ("clova", "hybrid")

    @property
    def need_ai(self):
        return self.mode in ("ai", "hybrid")

    def ai_sent(self):
        return any(self.ai["pages"])

    def set_mode(self, mode, mask=None, model=None):
        """읽기 전에 정한다. 이미 Qwen에 보낸 쪽이 있으면 가림 비율·모델은 처음 값을 그대로 쓴다(비교 기준이 달라지지 않게)."""
        with self.lock:
            self.mode = mode if mode in MODES else "clova"
            if self.need_ai and not self.ai_sent():
                if mask is not None:
                    self.ai["mask"] = float(mask)
                if model:
                    self.ai["model"] = model

    def todo(self):
        """(CLOVA로 읽을 쪽, Qwen으로 읽을 쪽): 아직 없거나 실패한 쪽만."""
        with self.lock:
            c = [i for i, x in enumerate(self.clova) if x is None or x.get("fail")] if self.need_clova else []
            a = [i for i, x in enumerate(self.ai["pages"]) if x is None or x.get("error")] if self.need_ai else []
            return c, a

    # ---------- 배치 ----------
    def opts(self, p):
        o = self.page_opts.get(int(p)) or {}
        return o.get("cols") or self.cols, o.get("side") or self.side

    def clova_page(self, p):
        """단 나누기·읽을 단을 적용한 CLOVA 쪽(줄에 box 좌표). 없으면 None."""
        raw = self.clova[int(p)]
        if not raw:
            return None
        cols, side = self.opts(p)
        return ocr_layout.arrange(raw, cols, side)

    def keep_of(self, p):
        """Qwen에 한쪽 단만 잘라 보낼 때 (단, 자를 위치 비율). 둘 다 읽으면 None."""
        _, side = self.opts(p)
        if side == "both":
            return None
        cp = self.clova_page(p)
        return side, ocr_layout.cut_frac(cp)

    def set_layout(self, p, cols=None, side=None):
        """p가 None이면 전체, 아니면 그 쪽만. 빈 값("")은 전체 설정을 따른다.
        줄이 바뀐 쪽은 차이별로 고른 것·제안을 지우고, 읽을 단이 바뀐 쪽은 Qwen 결과를 지운다(다시 읽어야 함).
        → {"ai_reset": 지운 Qwen 쪽 수}"""
        with self.lock:
            pages = range(self.n) if p is None else [int(p)]
            before = {i: (self.opts(i), self._lines(i)) for i in pages}
            if p is None:
                if cols in ocr_layout.MODES:
                    self.cols = cols
                if side in ocr_layout.SIDES:
                    self.side = side
            else:
                o = dict(self.page_opts.get(int(p)) or {})
                for k, v, ok in (("cols", cols, ocr_layout.MODES), ("side", side, ocr_layout.SIDES)):
                    if v is None:
                        continue
                    if v in ok:
                        o[k] = v
                    else:
                        o.pop(k, None)
                if o:
                    self.page_opts[int(p)] = o
                else:
                    self.page_opts.pop(int(p), None)
            ai_reset = 0
            for i in pages:
                (c0, s0), lines0 = before[i]
                c1, s1 = self.opts(i)
                rv = self.review.get(i)
                if s1 != s0 and self.ai["pages"][i]:
                    self.ai["pages"][i] = None
                    ai_reset += 1
                    if rv:
                        for k in ("d", "s"):
                            rv.pop(k, None)
                        if rv.get("page") in ("ai", "clova"):
                            rv.pop("page", None)
                elif rv and self._lines(i) != lines0:
                    rv.pop("d", None)
                    rv.pop("s", None)
            return {"ai_reset": ai_reset}

    def _lines(self, p):
        cp = self.clova_page(p)
        return [l["text"] for l in (cp or {}).get("lines") or []]

    # ---------- 비교·최종 글 ----------
    def _mask(self):
        return float(self.ai.get("mask") or 0)

    def ai_page(self, p):
        return self.ai["pages"][int(p)] if self.need_ai else None

    def compare(self, p):
        """하이브리드에서 두 결과가 다 있으면 비교 결과, 아니면 None."""
        if self.mode != "hybrid":
            return None
        cp, ap = self.clova_page(p), self.ai_page(p)
        if not cp or not ap or cp.get("fail") or ap.get("error"):
            return None
        return ocr_compare.compare(cp, ap, self._mask())

    def final_text(self, p):
        """정제에 쓸 그 쪽 글(사람이 고른 대로)."""
        with self.lock:
            p = int(p)
            rv = self.review.get(p) or {}
            figs = rv.get("figs", True)
            if rv.get("page") == "edit":
                return ocr_compare.nfc(rv.get("text") or "").strip()
            cp = self.clova_page(p) if self.need_clova else None
            ap = self.ai_page(p)
            cp_ok = bool(cp) and not cp.get("fail")
            ap_ok = bool(ap) and not ap.get("error")
            if cp_ok and ap_ok:
                return ocr_compare.resolve(ocr_compare.compare(cp, ap, self._mask()), rv, figs, ap)
            if cp_ok:
                return ocr.page_text(cp)
            if ap_ok:
                return ocr_compare.nfc(ocr_vision.page_text(ap, figs))
            return ""

    def read_ok(self, p):
        """필요한 엔진이 모두 읽었는지(실패는 읽지 못한 것으로)."""
        cp, ap = self.clova[p], self.ai["pages"][p]
        c = not self.need_clova or (cp is not None and not cp.get("fail"))
        a = not self.need_ai or (ap is not None and not ap.get("error"))
        return c and a

    def summary(self, p):
        """묶기 확인 썸네일 표시용."""
        with self.lock:
            cp, ap = self.clova[p], self.ai["pages"][p]
            c_read = cp is not None if self.need_clova else True
            a_read = ap is not None if self.need_ai else True
            out = {"index": p, "read": c_read and a_read, "fail": "", "ai_error": "", "chars": 0, "cols": 1, "warn": False,
                   "left": 0, "total": 0, "big": False, "own": bool(self.page_opts.get(p))}
            if self.need_clova and cp and cp.get("fail"):
                out["fail"] = cp["fail"]
            if self.need_ai and ap and ap.get("error"):
                out["ai_error"] = ap["error"]
            lay = (self.clova_page(p) or {}).get("layout") or {}
            out["cols"], out["warn"] = lay.get("cols", 1), bool(lay.get("warn"))
            out["chars"] = len(self.final_text(p))
            cmp = self.compare(p)
            if cmp:
                rv = self.review.get(p) or {}
                out["left"] = ocr_compare.unreviewed(cmp, rv)
                out["total"] = sum(1 for d in cmp["diffs"] if d["kind"] == "text")
                out["big"] = cmp["big"]
            return out

    def detail(self, p):
        """원본 보기 창: 한 쪽의 두 결과·차이·고른 것·배치."""
        with self.lock:
            p = int(p)
            cp = self.clova_page(p) if self.need_clova else None
            raw_cp = self.clova_page(p)  # Qwen만이어도 CLOVA를 읽어 둔 적이 있으면 경계선에 쓴다
            ap = self.ai_page(p)
            rv = self.review.get(p) or {}
            o = self.page_opts.get(p) or {}
            lay = (raw_cp or {}).get("layout") or {}
            out = {
                "ok": True, "index": p, "mode": self.mode, "read": self.read_ok(p) or bool(cp or ap),
                "clova": ocr.page_text(cp) if cp and not cp.get("fail") else None,
                "clova_fail": (cp or {}).get("fail") or "",
                "ai": ocr_vision.page_text(ap, False) if ap and not ap.get("error") else None,
                "ai_error": (ap or {}).get("error") or "" if ap else "",
                "ai_read": ap is not None if self.need_ai else None,
                "figures": (ap or {}).get("figures") or [] if ap and not ap.get("error") else [],
                "figs": rv.get("figs", True), "review": {k: rv[k] for k in ("d", "page", "text", "s") if k in rv},
                "text": self.final_text(p), "edited": rv.get("page") == "edit",
                "w": (raw_cp or {}).get("w") or 0, "h": (raw_cp or {}).get("h") or 0,
                "cols_own": o.get("cols", ""), "side_own": o.get("side", ""),
                "cols": lay.get("cols", 1), "line": lay.get("line"), "cut": lay.get("cut"), "side": self.opts(p)[1],
                "warn": bool(lay.get("warn")), "deg": lay.get("measured", 0),
                "mask": self._mask() if self.need_ai else 0, "cmp": None, "unreviewed": 0,
            }
            cmp = self.compare(p)
            if cmp:
                out["cmp"] = {k: cmp[k] for k in ("ratio", "big", "diffs")}
                out["unreviewed"] = ocr_compare.unreviewed(cmp, rv)
            return out

    def decide(self, p, data):
        """data: {"d": {id: {"pick", "text"}|None}} 차이별, {"page": "clova"|"ai"|"edit"|None, "text"} 쪽 통째,
        {"figs": bool}, {"all": "clova"|"ai"} 이 쪽 차이 모두, {"accept_suggest": True}, {"reset": True}."""
        with self.lock:
            p = int(p)
            rv = self.review.get(p) or {}
            data = data or {}
            if data.get("reset"):
                rv = {"figs": rv.get("figs", True)}
            if data.get("accept_suggest"):
                dd = rv.get("d") or {}
                for k, v in (rv.get("s") or {}).items():
                    dd[k] = {"pick": v["pick"]} if v["pick"] in ("clova", "ai") else {"pick": "edit", "text": v["text"]}
                rv["d"] = dd
            if "figs" in data:
                rv["figs"] = bool(data["figs"])
            if "page" in data:
                rv["page"] = data["page"] if data["page"] in ("clova", "ai", "edit") else None
                if data["page"] == "edit":
                    rv["text"] = str(data.get("text") or "")[:20000]
                if rv["page"] is None:
                    rv.pop("page")
            if data.get("all") in ("clova", "ai"):
                cmp = self.compare(p)
                if cmp and cmp["big"]:
                    rv["page"] = data["all"]
                elif cmp:
                    if rv.get("page") in ("clova", "ai"):
                        rv.pop("page")
                    rv["d"] = {**(rv.get("d") or {}), **{d["id"]: {"pick": data["all"]} for d in cmp["diffs"] if d["kind"] == "text"}}
            for k, v in (data.get("d") or {}).items():
                dd = rv.get("d") or {}
                if v is None or not isinstance(v, dict) or v.get("pick") not in ("clova", "ai", "edit"):
                    dd.pop(k, None)
                else:
                    dd[k] = {"pick": v["pick"], **({"text": str(v.get("text") or "")[:2000]} if v["pick"] == "edit" else {})}
                rv["d"] = dd
            self.review[p] = rv
            return self.detail(p)

    def scrubber(self, cmp):
        """AI 제안에 보낼 글에서 신원 지우기: 이 쪽에서 규칙으로 읽은 학번·이름, '학번:'·'이름:'·'n학년 n반 n번' 뒤의 값."""
        values = []
        for t in (cmp["a"], cmp["b"]):
            no, name = scan.extract_ids(t, [])
            values += [no, name, *scan.label_names(t, [])]
        regexes = [scan.RE_NAME_LABEL, scan.RE_NO_LABEL, scan.RE_GCN]
        values = sorted({v for v in values if v}, key=len, reverse=True)
        return lambda text: ocr_compare.redact(text, values, regexes)

    def store_suggest(self, p, sugg, cost):
        with self.lock:
            rv = self.review.get(int(p)) or {}
            rv["s"] = sugg
            self.review[int(p)] = rv
            self.fix_cost = round(self.fix_cost + float(cost or 0), 6)
