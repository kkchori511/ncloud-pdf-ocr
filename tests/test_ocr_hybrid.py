"""하이브리드 OCR 비교 엔진과 Qwen 그림(생기부착즙기 시험에서 가져옴): 더미 학습지의 실제 응답으로 비교·최종 글, 머리글 가림."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import ocr, ocr_compare as oc, ocr_vision as ov  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "ocr_dummy")
PDF = os.path.join(FX, "dummy.pdf")
CLOVA = json.load(open(os.path.join(FX, "clova_raw.json"), encoding="utf-8"))
QWEN = [json.load(open(os.path.join(FX, f"qwen_p{i}.json"), encoding="utf-8"))["result"] for i in (1, 2)]
REAL_RENDER = ov.render_png


def test_pure():
    """비교 엔진: 가린 머리글 빼기, ℃/°C 같게, 그림 속 글자, 고른 대로 최종 글."""
    cp = ocr.image_lines(CLOVA["images"][0])
    assert cp["lines"][0]["box"] and cp["w"] == 1190
    c = oc.compare(cp, QWEN[0], 0.14)
    got = [(d["kind"], d["clova"], d["ai"]) for d in c["diffs"]]
    assert got == [("text", "햇", "했"), ("figure", "온도계\n비커\n알코올램프", ""), ("text", "열", "얼")], got
    assert oc.unreviewed(c, {}) == 2
    t = oc.resolve(c, {}, True)
    assert "실험햇다" in t and "넣어 준 열은" in t and "학번: 10399" in t  # 기본은 CLOVA, 머리글은 그대로
    assert "[그림:" in t and "\n온도계\n비커\n" not in t  # 그림 설명이 이름표 줄을 대신
    t2 = oc.resolve(c, {"d": {"d0": {"pick": "ai"}, "d2": {"pick": "edit", "text": "열"}}}, False)
    assert "실험했다" in t2 and "[그림:" not in t2 and "\n온도계\n비커\n알코올램프\n" in t2
    c2 = oc.compare(ocr.image_lines(CLOVA["images"][1]), QWEN[1], 0.14)
    assert [(d["clova"], d["ai"]) for d in c2["diffs"]] == [("잎", "일"), ("골라", ""), (".", "")]
    t3 = oc.resolve(c2, {"d": {"d2": {"pick": "ai"}}}, True)
    assert "5분에는 3개, 10분에는" in t3  # 지운 뒤 겹 띄어쓰기 없음
    # 머리글을 안 가렸으면 머리글이 차이로 잡힌다(가림 비율을 맞게 넘기는지 확인용)
    assert any("10399" in d["clova"] for d in oc.compare(cp, QWEN[0], 0)["diffs"])
    # 크게 다른 페이지는 통째로
    big = oc.compare(cp, {"lines": [{"text": "전혀 다른 글자들 가나다라마바사", "unreadable": False}], "figures": []}, 0.14)
    assert big["big"] and oc.unreviewed(big, {}) == 1 and oc.unreviewed(big, {"page": "ai"}) == 0
    # AI 단독 글과 그림 설명 끄기
    assert ov.page_text(QWEN[0], True).count("[그림:") == 1 and "[그림:" not in ov.page_text(QWEN[0], False)
    # 그림 설명이 그림 없는 쪽에 끼어들어도 끄면 빠진다
    # 모델 답 검사
    try:
        ov.parse("not json"); raise AssertionError
    except ov.VisionError:
        pass
    v = ov.parse('```json\n{"lines":[{"text":"가","unreadable":false}],"figures":[{"after_line":99,"desc":"그림"}]}\n```')
    assert v["figures"][0]["after_line"] == 0


def test_mask_png():
    """머리글 가림: 위쪽 비율만큼 흰색."""
    import struct, zlib
    png = REAL_RENDER(PDF, 0, 0.14, width=300)
    w, h = struct.unpack(">II", png[16:24])
    # IDAT 풀어서 맨 윗줄과 가린 영역 바로 아래 줄 비교
    idat = b"".join(png[i + 8:i + 8 + struct.unpack(">I", png[i:i + 4])[0]] for i in range(8, len(png) - 12) if png[i + 4:i + 8] == b"IDAT")
    raw = zlib.decompress(idat)
    ch = (len(raw) // h - 1) // w
    row = lambda y: raw[y * (w * ch + 1) + 1:(y + 1) * (w * ch + 1)]
    assert set(row(int(h * 0.14) - 1)) == {255}
    assert ov.payload("m", png)["provider"]["data_collection"] == "deny" and ov.payload("m", png)["provider"]["zdr"] is True


if __name__ == "__main__":
    test_pure()
    test_mask_png()
    print("ok")
