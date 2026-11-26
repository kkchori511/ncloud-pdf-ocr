"""스캔 흐름: OCR(가짜 CLOVA) → 쪽 요약·원본 그림 → 묶기 미리보기(규칙 학번·이름) → 정제(가짜 AI).
학번·이름을 규칙으로 찾은 학생은 인적사항을 지운 본문만, 못 찾은 학생만 머리글을 함께 보낸다."""
import json
import os
import sys
import tempfile
import time

os.environ["AKPIL_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, ocr, openrouter  # noqa: E402
from app.api import Api  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PDF = os.path.join(HERE, "fixtures", "scan_sample.pdf")  # 6쪽
OCR_TEXT = ["2학년 3반 1번 이름: 김철수\n진로 독서 감상문\n책을 읽고 감명받음.", "(뒷면) 실험 실패를 기록하겠다.",
            "학번 20302 성명 이영희\n직업 탐색 보고서", "(뒷면) 윤리 문제도 생각함.", "꿈 발표 준비\n열심히 하겠다", ""]
STOP_AFTER = [None]


def fake_ocr(path, url, secret, lang, progress=None, should_stop=None, pages=None, on_chunk=None):
    want = list(range(6)) if pages is None else pages
    if progress:
        progress(0, 1, 6)
    got = {i: {"lines": [{"text": l, "box": None} for l in OCR_TEXT[i].split("\n") if l], "w": 0, "h": 0, "fail": ""}
           for i in want}
    if STOP_AFTER[0] is not None:  # 앞 몇 쪽만 받고 중지
        got = {i: v for i, v in got.items() if i < STOP_AFTER[0]}
        on_chunk(got)
        raise ocr.Stopped()
    on_chunk(got)
    return [got.get(i) for i in range(6)]


SENT = []


def fake_chat(key, model, system, user):
    SENT.append((system, user))
    if "<머리글>" in user:
        return json.dumps({"student_id": "20303", "name": "박민순", "text": "AI정제"}), 0.01
    return json.dumps({"text": "정제:" + user}), 0.01


ocr.ocr_pdf_pages = fake_ocr
openrouter.chat = fake_chat
openrouter.balance = lambda key: 1.0


class FakeWindow:
    def __init__(self):
        self.events = []

    def create_file_dialog(self, *a, **k):
        return (PDF,)

    def evaluate_js(self, js):
        self.events.append(json.loads(js[len("window.onAppEvent("):-1]))


def wait_idle(api):
    for _ in range(200):
        if api._busy.acquire(blocking=False):
            api._busy.release()
            return
        time.sleep(0.02)
    raise AssertionError("작업이 끝나지 않음")


def last(win, typ):
    return [e for e in win.events if e["type"] == typ][-1]


def test():
    config.save({"clova_url": "https://x", "clova_secret": "s", "openrouter_key": "k", "refine_model": "m"})
    api, win = Api(), FakeWindow()
    api.attach(win)
    r = api.pick_pdf()
    assert r["ok"] and r["pages"] == 6, r

    # 중지: 앞 3쪽만 읽힘 → 나머지는 '읽지 않음', 이어 읽기
    STOP_AFTER[0] = 3
    assert api.start_ocr()["ok"]
    wait_idle(api)
    ev = last(win, "ocr_done")
    assert ev["stopped"] and [p["read"] for p in ev["pages"]] == [True, True, True, False, False, False], ev
    STOP_AFTER[0] = None
    assert api.start_ocr(True)["ok"]
    wait_idle(api)
    ev = last(win, "ocr_done")
    assert not ev["stopped"] and all(p["read"] for p in ev["pages"]), ev
    assert api.start_ocr(True)["error"] == "다시 읽을 쪽이 없습니다."

    # 원본 그림·쪽 글자
    img = api.page_image(0, 200)
    assert img["ok"] and img["src"].startswith("data:image/png;base64,"), img
    d = api.page_detail(2)
    assert d["ok"] and "이영희" in d["text"], d
    assert api.set_layout(2, "off")["detail"]["cols_own"] == "off"
    assert api.set_layout(2, "")["detail"]["cols_own"] == ""

    # 묶기: 3명 × 2쪽, 마지막 백지 쪽 빼기
    groups = api.default_groups(2)
    assert groups == [[0, 1], [2, 3], [4, 5]], groups
    groups[2] = [4]
    pv = api.preview_groups(groups)
    assert [(g["student_id"], g["name"]) for g in pv] == [("20301", "김철수"), ("20302", "이영희"), ("", "")], pv

    assert api.start_refine(groups)["ok"]
    wait_idle(api)
    st = last(win, "stage1_done")["students"]
    assert [(s["student_id"], s["name"], s["id_by"]) for s in st] == [
        ("20301", "김철수", "rule"), ("20302", "이영희", "rule"), ("20303", "박민순", "ai")], st
    assert st[0]["pages"] == "1-2" and st[2]["pages"] == "5" and st[0]["page_list"] == [0, 1]
    # 규칙으로 찾은 학생: 학번·이름이 AI로 가지 않음
    plain = [u for s, u in SENT if "<머리글>" not in u]
    assert len(plain) == 2 and not any("김철수" in u or "20301" in u or "이영희" in u or "20302" in u for u in plain), plain
    assert all("student_id" not in s for s, u in SENT if "<머리글>" not in u)
    assert "책을 읽고 감명받음." in st[0]["original"]
    assert st[2]["original"] == "AI정제"
    print("ok")


if __name__ == "__main__":
    test()
