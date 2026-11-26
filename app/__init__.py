APP_NAME = "악필교정기"
VERSION = "1.2.1"
MAKER = "Made by 김한량 디지털 공방"
TAGLINE = "손글씨 과제 스캔을 읽고 다듬어 엑셀로 정리합니다."
GITHUB_REPO = "kkchori511/ncloud-pdf-ocr"
EXE_NAME = "악필교정기.exe"
PAGES_URL = "https://kkchori511.github.io/ncloud-pdf-ocr/"


def web_ok() -> bool:
    """화면 파일이 함께 묶였는지(빌드 확인용)."""
    from .paths import web_dir

    return all((web_dir() / f).exists() for f in ("index.html", "app.js", "style.css", "guide.html", "face.png", "intro.html", "splash_icon.png",
                                                 "fonts/intro-Paperlogy-8ExtraBold.woff2", "fonts/intro-Paperlogy-6SemiBold.woff2"))
