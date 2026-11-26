# 악필교정기

학생 과제 스캔 PDF를 CLOVA OCR로 읽고, OpenRouter AI로 정제·변환하여 Excel(xlsx)로 저장하는 Windows 프로그램입니다.
Made by 김한량 디지털 공방 · 버전 1.2.0

**내려받기**: https://kkchori511.github.io/ncloud-pdf-ocr/ (앱을 켜면 새 버전을 확인하고 [지금 업데이트]로 바꿉니다)

## 개발 실행

Python 3.12를 사용합니다. Windows PowerShell에서 프로젝트 폴더를 열고 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

앱에서 본인의 CLOVA Invoke URL·Secret Key와 OpenRouter API 키를 설정합니다.
키는 프로그램 옆 `악필교정기_설정.json`에 평문 저장되므로 공용 PC에서는 사용 후 앱의 키 초기화를 실행하세요.

## Windows 빌드

위 가상환경을 활성화한 Windows에서 실행합니다.

```powershell
pip install -r requirements.txt pyinstaller
pyinstaller packaging/akpil.spec
```

결과는 콘솔 창 없는 단일 파일 `dist/악필교정기.exe`입니다. 화면 리소스와 아이콘, Windows 버전 정보가 포함됩니다.

GitHub Actions의 **Build Windows → Run workflow**로도 빌드할 수 있습니다.
완료 후 `akpil-windows` 아티팩트에서 exe를 내려받으세요.
`v*` 태그(예: `v1.1.0`)를 push하면 같은 빌드가 실행되고 GitHub Release에 exe(`Akpil.exe`)가 첨부됩니다.
이어서 같은 exe와 내려받기 페이지(`site/`)가 `gh-pages` 브랜치에 올라가 GitHub Pages로 배포됩니다.
태그는 `app/__init__.py`의 `VERSION`과 같아야 합니다(다르면 빌드가 멈춥니다).

빌드 전에 `python tests/run_all.py`로 시험을 돌립니다(CI도 같은 시험, exe 자체 시험, 업데이트 교체 시험을 합니다).

상세 기능은 [명세](docs/SPEC.md)를 참고하세요.
