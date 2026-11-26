DEFAULT_REFINE_PROMPTS = {
    "A": (
        "아래는 학생이 손으로 쓴 과제를 스캔해 OCR로 읽은 텍스트다.\n"
        "- OCR 오인식, 맞춤법, 띄어쓰기만 바로잡는다.\n"
        "- 학생의 문장 구조, 표현, 내용은 바꾸지 않는다.\n"
        "- 알아볼 수 없는 부분은 앞뒤 문맥으로 가장 그럴듯한 낱말로 고치되, 새 내용을 지어내지 않는다."
    ),
    "B": (
        "아래는 학생이 손으로 쓴 과제를 스캔해 OCR로 읽은 텍스트다.\n"
        "- OCR 오인식, 맞춤법, 띄어쓰기를 바로잡는다.\n"
        "- 끊기거나 불완전한 문장은 학생의 의도를 살려 자연스럽게 완성한다.\n"
        "- 학생이 쓰지 않은 새 내용은 추가하지 않는다.\n"
        "- 결과는 문단이 정리된 정상적인 문서 형태로 만든다."
    ),
}

# 정제 결과 형식은 사용자가 고치지 못하게 코드에 고정한다.
# 학번·이름은 앱이 규칙으로 먼저 찾고 그 줄을 지운 본문만 보낸다(REFINE_FORMAT).
# 못 찾은 학생만 첫 쪽 머리글을 함께 보내 AI가 찾게 한다(REFINE_FORMAT_IDS).
REFINE_FORMAT = (
    "\n\n[추가 규칙]\n"
    "- 과제 양식의 머리글(제목 칸, 날짜, 반·번호 같은 라벨)이 남아 있으면 본문에서 뺀다.\n"
    "- 다른 설명 없이 아래 JSON 하나만 출력한다.\n"
    '{"text": "정제한 본문"}'
)

REFINE_FORMAT_IDS = (
    "\n\n[추가 규칙]\n"
    "- 입력은 <머리글>(과제 첫 쪽 윗부분)과 <본문>으로 나뉜다. 정제하는 대상은 <본문>이다.\n"
    "- <머리글>에서 학번과 이름을 찾는다. 학번은 숫자만 적는다. 찾지 못한 값은 빈 문자열로 둔다.\n"
    "- 학번, 이름, 과제 양식의 머리글(제목 칸, '학번', '이름' 같은 라벨)은 본문에서 뺀다.\n"
    "- 다른 설명 없이 아래 JSON 하나만 출력한다.\n"
    '{"student_id": "학번", "name": "이름", "text": "정제한 본문"}'
)

DEFAULT_TRANSFORM_TABS = [
    {
        "name": "요약",
        "prompt": "다음 학생 글의 핵심 내용을 문단 하나로 요약하라. 학생의 관점과 주장을 유지한다.",
        "example": "",
    },
    {"name": "프롬프트 2", "prompt": "", "example": ""},
    {"name": "프롬프트 3", "prompt": "", "example": ""},
    {"name": "프롬프트 4", "prompt": "", "example": ""},
    {"name": "프롬프트 5", "prompt": "", "example": ""},
]


def length_rule(target_bytes: int) -> str:
    if not target_bytes or target_bytes <= 0:
        return ""
    # 한글 3바이트·공백 1바이트로 섞이면 한 글자에 평균 2.5바이트 정도다.
    approx_chars = round(target_bytes / 2.5)
    return (
        f"\n\n[분량]\n"
        f"- 목표 분량은 약 {target_bytes}바이트다. 한글 한 글자는 3바이트, "
        f"영어·숫자·기호·공백은 1바이트로 센다.\n"
        f"- 한글 위주라면 공백 포함 약 {approx_chars}자 안팎이다."
    )


def example_rule(example: str) -> str:
    example = (example or "").strip()
    if not example:
        return ""
    return (
        "\n\n[예시]\n"
        "- 아래 예시의 문체, 형식, 분량을 참고한다. 예시의 내용은 베끼지 않고, 주어진 학생 글의 내용만 쓴다.\n"
        f"<예시>\n{example}\n</예시>"
    )


TRANSFORM_FORMAT = "\n\n[출력]\n- 결과 본문만 출력한다. 제목, 설명, 따옴표를 붙이지 않는다."
