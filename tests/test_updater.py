"""자동 업데이트: 새 버전 판단, Release 응답 해석(Akpil.exe·SHA-256), 예비 경로(GitHub Pages) 주소, 내려받기 검증."""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

os.environ["AKPIL_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import VERSION, updater  # noqa: E402

BODY = b"new exe bytes"
SHA = hashlib.sha256(BODY).hexdigest()


class Res:
    def __init__(self, status, data=None, content=b""):
        self.status_code, self._data, self.content = status, data, content
        self.ok = 200 <= status < 300
        self.headers = {"Content-Length": str(len(content))}

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            import requests
            raise requests.HTTPError(response=self)

    def iter_content(self, n):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test():
    assert updater.is_newer("v9.0.0") and not updater.is_newer(f"v{VERSION}") and not updater.is_newer("v0.9")
    assert updater.MIRROR.format(version="1.2.3") == "https://kkchori511.github.io/ncloud-pdf-ocr/Akpil-1.2.3.exe"

    assert updater.clean_notes("## 새것\r\n- **가**: 나\r\n\r\n**Full Changelog**: https://x") == "[새것]\n- 가: 나"
    rel = {"tag_name": "v9.0.0", "body": "노트\n\n**Full Changelog**: https://x", "assets": [
        {"name": "other.exe", "browser_download_url": "x"},
        {"name": "Akpil.exe", "size": len(BODY), "digest": "sha256:" + SHA,
         "browser_download_url": "https://github.com/kkchori511/ncloud-pdf-ocr/releases/download/v9.0.0/Akpil.exe"}]}
    updater.requests.get = lambda *a, **k: Res(200, rel)
    info = updater.check()
    assert info["available"] and info["version"] == "9.0.0" and info["sha256"] == SHA and info["notes"] == "노트", info
    assert info["url"].endswith("/Akpil.exe") and info["can_apply"] is False  # 개발 실행(mac)에서는 교체하지 않음

    updater.requests.get = lambda *a, **k: Res(200, {**rel, "tag_name": f"v{VERSION}"})
    assert updater.check()["available"] is False

    # 내려받기: 릴리스 서버가 막히면 예비 경로에서 받고 SHA-256을 확인한다
    seen = []

    def get(url, **k):
        seen.append(url)
        if "github.com/" in url and "/releases/download/" in url:
            raise updater.requests.ConnectionError("막힘")
        return Res(200, content=BODY)

    updater.requests.get = get
    tmp = Path(tempfile.mkdtemp()) / "악필교정기.exe"
    updater.current_exe = lambda: tmp
    path = updater.download(info, tries=1, wait=0)
    assert Path(path).read_bytes() == BODY and seen[-1] == updater.MIRROR.format(version="9.0.0"), seen

    # 검증값이 다르면 받지 않는다
    try:
        updater.download({**info, "sha256": "0" * 64}, tries=1, wait=0)
        raise AssertionError("검증 실패인데 통과")
    except updater.UpdateError:
        pass
    print("ok")


if __name__ == "__main__":
    test()
