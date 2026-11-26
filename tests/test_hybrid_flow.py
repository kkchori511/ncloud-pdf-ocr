"""Qwen·하이브리드 흐름(가짜 CLOVA·Qwen 응답, 더미 학습지): 가림 비율로 보내기, 동의 없으면 0% 막기,
이미 읽은 쪽은 다시 부르지 않기, 차이 고르기·최종 글, AI 제안(학번·이름 ○), 읽을 단 바꾸면 Qwen 결과 지우기."""
import json
import os
import sys
import tempfile
import time

os.environ["AKPIL_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, ocr, ocr_vision as ov, openrouter  # noqa: E402
from app.api import Api  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "ocr_dummy")
PDF = os.path.join(FX, "dummy.pdf")
CLOVA = json.load(open(os.path.join(FX, "clova_raw.json"), encoding="utf-8"))
QWEN = [json.load(open(os.path.join(FX, f"qwen_p{i}.json"), encoding="utf-8"))["result"] for i in (1, 2)]
CALLS = {"clova": 0, "ai": [], "sent": []}


def fake_call_once(url, secret, data, name, lang):
    CALLS["clova"] += 1
    return CLOVA


def fake_render(pdf, page, mask=0.0, width=ov.RENDER_WIDTH, keep=None):
    CALLS["sent"].append((page, mask, keep))
    return f"P{page}".encode()


def fake_read_page(key, model, png, post=None):
    page = int(png.decode()[1:])
    CALLS["ai"].append(page)
    q = QWEN[page]
    return {"lines": q["lines"], "figures": q["figures"], "cost": 0.0015, "provider": "x", "model": model}


SENT = []


def fake_chat(key, model, system, user):
    SENT.append(user)
    if "CLOVA:" in user:  # AI 제안
        return json.dumps({"items": [{"id": "d0", "pick": "ai", "text": "", "reason": "문맥"}]}), 0.0001
    return json.dumps({"text": "정제"}), 0.0


ocr.call_once = fake_call_once
ov.read_page = fake_read_page
ov.render_png = fake_render
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
    for _ in range(300):
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
    assert api.pick_pdf()["ok"]
    assert api.mask_preview(0.12)["ok"] and CALLS["sent"] == [(0, 0.12, None)]  # 첫 쪽을 보낼 모양으로
    CALLS["sent"].clear()

    # 가림 0%는 동의 없으면 막힘
    r = api.start_ocr(False, "hybrid", 0)
    assert not r["ok"] and "동의" in r["error"], r

    # 하이브리드: CLOVA 한 번 + Qwen 쪽마다, 가림 14%로
    assert api.start_ocr(False, "hybrid", 0.14)["ok"]
    wait_idle(api)
    ev = last(win, "ocr_done")
    assert ev["mode"] == "hybrid" and all(p["read"] for p in ev["pages"]), ev
    assert CALLS["clova"] == 1 and sorted(CALLS["ai"]) == [0, 1] and {m for _, m, _ in CALLS["sent"]} == {0.14}, (CALLS, [e for e in win.events if e["type"] in ("error", "step")][-3:])
    assert abs(ev["cost"] - 0.003) < 1e-9
    assert ev["pages"][0]["total"] == 2 and ev["pages"][0]["left"] == 2, ev["pages"][0]

    # 이미 읽은 쪽은 다시 부르지 않음
    assert api.start_ocr(True, "hybrid", 0.3)["error"] == "다시 읽을 쪽이 없습니다."

    # 차이 고르기 → 최종 글
    d = api.page_detail(0)
    assert [x["clova"] for x in d["cmp"]["diffs"] if x["kind"] == "text"] == ["햇", "열"]
    assert "실험햇다" in d["text"] and "학번: 10399" in d["text"]  # 기본은 CLOVA, 가린 머리글은 CLOVA 그대로
    d = api.decide(0, {"d": {"d0": {"pick": "ai"}}})
    assert "실험했다" in d["text"] and d["unreviewed"] == 1
    d = api.decide(0, {"all": "clova"})
    assert d["unreviewed"] == 0 and "실험햇다" in d["text"]
    d = api.decide(0, {"page": "edit", "text": "직접 고친 글"})
    assert d["text"] == "직접 고친 글" and d["edited"]
    d = api.decide(0, {"reset": True})
    assert d["unreviewed"] == 2

    # AI 제안: 학번·이름은 ○로 지워 보냄, 제안은 미리 골라 두기만
    s = api.suggest(0)
    assert s["review"]["s"]["d0"]["pick"] == "ai" and s["unreviewed"] == 2, s
    assert "10399" not in SENT[-1]
    d = api.decide(0, {"accept_suggest": True})
    assert "실험했다" in d["text"] and d["unreviewed"] == 1

    # 확대 그림
    x = next(x for x in d["cmp"]["diffs"] if x["kind"] == "text")
    assert api.diff_crop(0, x["box"], d["w"], d["h"], x["clip"])["src"].startswith("data:image/png")

    # 읽을 단을 바꾸면 그 쪽 Qwen 결과는 지움 → 남은 쪽만 다시 읽음(그 단만 잘라 보냄)
    r = api.set_layout(1, None, "right")
    assert r["ai_reset"] == 1 and r["detail"]["side_own"] == "right"
    CALLS["ai"].clear()
    CALLS["sent"].clear()
    assert api.start_ocr(True, "hybrid", 0.3)["ok"]
    wait_idle(api)
    assert CALLS["ai"] == [1] and CALLS["clova"] == 1
    page, mask, keep = CALLS["sent"][0]
    assert page == 1 and mask == 0.14 and keep[0] == "right", CALLS["sent"]  # 가림 비율은 처음 값 그대로

    # Qwen만: CLOVA 키 없이도
    config.save({"clova_url": "", "clova_secret": ""})
    assert api.start_ocr(False, "ai", 0.12)["ok"]
    wait_idle(api)
    ev = last(win, "ocr_done")
    assert ev["mode"] == "ai" and all(p["read"] for p in ev["pages"]) and CALLS["clova"] == 1
    assert "[그림:" in api.page_detail(0)["text"]
    r = api.start_ocr(False, "clova", 0)
    assert not r["ok"] and "CLOVA" in r["error"]

    # 정제는 고른 글로
    assert api.start_refine([[0], [1]])["ok"]
    wait_idle(api)
    st = last(win, "stage1_done")["students"]
    assert len(st) == 2 and not any(s["error"] for s in st), st
    print("ok")


if __name__ == "__main__":
    test()
