import os
import sys
from pathlib import Path


def resource_dir() -> Path:
    """번들된 리소스(app/web 등)의 기준 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """설정 파일을 둘 폴더: exe 옆, 개발 중에는 프로젝트 루트. 시험은 AKPIL_DATA_DIR로 바꾼다."""
    if os.environ.get("AKPIL_DATA_DIR"):
        return Path(os.environ["AKPIL_DATA_DIR"])
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    return resource_dir() / "app" / "web"
