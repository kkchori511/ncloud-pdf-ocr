import webview

from . import APP_NAME, VERSION
from .api import Api


def main():
    api = Api()
    window = webview.create_window(
        f"{APP_NAME} {VERSION}",
        url="app/web/index.html",  # pywebview가 앱 루트(_MEIPASS) 기준으로 내장 서버에서 제공
        js_api=api,
        width=1280,
        height=900,
        min_size=(960, 640),
    )
    api.attach(window)
    webview.start()


if __name__ == "__main__":
    main()
