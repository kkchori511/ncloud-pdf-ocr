from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HEADERS = ["학번", "이름", "원본", "변환"]
WIDTHS = [14, 12, 70, 70]


def write_xlsx(path: str, rows: list[dict], include_transform: bool) -> None:
    """rows: [{student_id, name, original, transformed}]"""
    cols = 4 if include_transform else 3
    wb = Workbook()
    ws = wb.active
    ws.title = "결과"
    ws.append(HEADERS[:cols])
    for c in range(1, cols + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E8F8EE")
        ws.column_dimensions[cell.column_letter].width = WIDTHS[c - 1]
    for r in rows:
        values = [r.get("student_id", ""), r.get("name", ""), r.get("original", "")]
        if include_transform:
            values.append(r.get("transformed", ""))
        ws.append(values)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrap
    # 학번이 숫자로 바뀌어 앞자리 0이 사라지지 않게 문자열로 둔다
    for row in ws.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "@"
    ws.freeze_panes = "A2"
    wb.save(path)
