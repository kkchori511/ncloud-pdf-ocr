"""GitHub Release 자동 업데이트(생기부착즙기 app/updater.py에서 가져와 줄임).

- 실행할 때 최신 Release를 확인하고 화면에 알린다(작업 중 교체하지 않음).
- [지금 업데이트]: 새 exe 내려받기(SHA-256 확인) → 이름 바꿔 교체 → 새 exe 실행 → 이 앱 종료.
  · 실행 중인 exe도 '이름 바꾸기'는 된다: 지금 exe → .old, 내려받은 .new → exe.
  · PowerShell·배치 같은 외부 스크립트를 쓰지 않는다(학교 PC 보안 프로그램이 막는 경우가 있음).
  · 새 앱이 켜지면 남은 .old/.new를 지운다.
- Windows exe(frozen)에서만 교체한다. 개발 실행에서는 확인만 한다.
"""
import datetime as dt
import hashlib
import os
import re
import subprocess
import sys

import requests

from . import EXE_NAME, GITHUB_REPO, VERSION
from pathlib import Path

from .paths import data_dir as app_dir, data_dir

API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_NAME = "Akpil.exe"
# 예비 경로: 일부 학교·통신사 망은 GitHub 릴리스 파일 서버(release-assets.githubusercontent.com)를 막는다.
# 릴리스 때 CI가 같은 exe를 GitHub Pages(gh-pages 브랜치, 최신 1개)에도 올린다. 어디서 받든 SHA-256으로 같은 파일인지 확인한다.
MIRROR = f"https://{GITHUB_REPO.split('/')[0]}.github.io/{GITHUB_REPO.split('/')[1]}/Akpil-{{version}}.exe"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


def clean_notes(body: str) -> str:
    """릴리스 노트를 앱 알림 창(일반 글)에 맞게: 'Full Changelog' 줄을 빼고, 제목(##)은 [제목], 굵게(**)는 기호만 지운다."""
    out = []
    for l in (body or "").replace("\r", "").split("\n"):
        if l.startswith("**Full Changelog"):
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", l)
        if m:
            l = f"[{m.group(1).strip()}]"
        out.append(l.replace("**", ""))
    return "\n".join(out).strip()


class UpdateError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def parse_version(v: str):
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", (v or "").strip())
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(remote: str, local: str = VERSION) -> bool:
    r, l = parse_version(remote), parse_version(local)
    return bool(r and l and r > l)


def can_apply() -> bool:
    return bool(getattr(sys, "frozen", False)) and os.name == "nt"


def check() -> dict:
    try:
        res = requests.get(API, timeout=15, headers={"Accept": "application/vnd.github+json"})
    except requests.RequestException:
        return {"ok": False, "error": "업데이트를 확인하지 못했습니다(인터넷 연결)."}
    if res.status_code == 404:
        return {"ok": True, "available": False}
    if not res.ok:
        return {"ok": False, "error": f"업데이트를 확인하지 못했습니다(HTTP {res.status_code})."}
    data = res.json()
    tag = data.get("tag_name", "")
    assets = [a for a in data.get("assets", []) if a.get("name", "").lower().endswith(".exe")]
    asset = next((a for a in assets if a.get("name") == ASSET_NAME), assets[0] if assets else None)
    if not is_newer(tag) or not asset:
        return {"ok": True, "available": False, "latest": tag}
    digest = asset.get("digest") or ""
    sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        sha = ""
    return {
        "ok": True,
        "available": True,
        "version": tag.lstrip("v"),
        "notes": clean_notes(data.get("body") or "")[:2000],
        "url": asset.get("browser_download_url"),
        "size": asset.get("size"),
        "sha256": sha,
        "can_apply": can_apply() and bool(sha),
    }


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url, dest: Path, size_hint=0, progress=None):
    """dest로 받는다. 이미 받은 부분이 있으면 이어 받는다(서버가 Range를 받지 않으면 처음부터)."""
    done = dest.stat().st_size if dest.exists() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    with requests.get(url, stream=True, timeout=(15, 60), headers=headers) as res:
        if done and res.status_code != 206:
            done = 0
        res.raise_for_status()
        total = done + int(res.headers.get("Content-Length") or 0) or int(size_hint or 0)
        with open(dest, "ab" if done else "wb") as f:
            for chunk in res.iter_content(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done, total)


def _why(e) -> str:
    """기록용 짧은 원인(주소에 붙은 서명 값은 남기지 않는다)."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    if isinstance(e, requests.exceptions.SSLError):
        return "SSLError(인증서·보안 장비)"
    if isinstance(e, requests.Timeout):
        return "Timeout(응답 없음)"
    if isinstance(e, requests.ConnectionError):
        return "ConnectionError(연결 막힘·끊김)"
    return e.__class__.__name__


def download(info: dict, progress=None, tries=3, wait=1.0) -> str:
    """GitHub 릴리스 파일 → 안 되면 예비 경로(GitHub Pages). 경로마다 끊기면 이어 받기로 몇 번 다시 시도한다."""
    import time

    url = info.get("url") or ""
    if not url.startswith(f"https://github.com/{GITHUB_REPO}/releases/download/"):
        raise UpdateError("업데이트 주소가 올바르지 않습니다.")
    sha = (info.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise UpdateError("검증값(SHA-256)이 없어 자동 업데이트하지 않습니다. 릴리스 페이지에서 직접 내려받아 주세요.")
    dest = current_exe().with_name(current_exe().name + ".new")
    sources = [("GitHub 릴리스", url)]
    if re.fullmatch(r"[0-9A-Za-z.\-]+", info.get("version") or ""):
        sources.append(("예비 경로", MIRROR.format(version=info["version"])))
    why = []
    for label, src in sources:
        try:
            dest.unlink(missing_ok=True)
        except OSError as e:
            raise UpdateError(f"이전에 받다 만 파일을 지우지 못했습니다({e.__class__.__name__}).") from e
        for i in range(tries):
            try:
                _fetch(src, dest, info.get("size"), progress)
                break
            except (requests.RequestException, OSError) as e:
                why.append(f"{label} {i + 1}회: {_why(e)}")
                if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code == 404:
                    break  # 없는 파일: 다시 해도 같다
                if i + 1 < tries:
                    time.sleep(wait)
        else:
            continue
        if dest.exists() and _sha256(dest) == sha:
            if why:
                log("내려받기: " + "; ".join(why) + f" → {label}에서 받음")
            return str(dest)
        why.append(f"{label}: 검증(SHA-256) 실패")
    dest.unlink(missing_ok=True)
    log("내려받기 실패: " + "; ".join(why))
    raise UpdateError("새 버전을 내려받지 못했습니다. 지금 인터넷망이 GitHub 파일 서버를 막고 있을 수 있어요. "
                      "다른 인터넷(휴대폰 핫스폿 등)에서 다시 시도하거나, 릴리스 페이지에서 직접 받아 exe를 바꿔 주세요.")


def current_exe() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return app_dir() / EXE_NAME


def log(msg: str):
    """업데이트 기록(문제 확인용): 프로그램 폴더/악필교정기_업데이트_기록.txt"""
    try:
        with open(data_dir() / "악필교정기_업데이트_기록.txt", "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def _clean_env():
    """PyInstaller onefile 앱이 다른 onefile 앱을 띄울 때 압축 해제 폴더를 공유하지 않게 한다."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def swap_only(new_path: str):
    """exe만 새 버전으로 바꿔 둔다(실행은 사용자가 다시 켤 때). 보안 프로그램이 자동 실행을 막는 학교 PC용."""
    swap_and_launch(new_path, launch=False)


def swap_and_launch(new_path: str, args=(), before_launch=None, launch=True):
    """exe 교체 후 새 exe를 띄운다. 실패하면 원래대로 되돌리고 UpdateError.
    before_launch: 새 exe를 띄우기 바로 전에 부른다(실행 잠금 넘겨주기). 이름 바꾸기가 실패하면 부르지 않는다."""
    if not can_apply():
        raise UpdateError("Windows exe에서만 업데이트할 수 있습니다.")
    exe = current_exe()
    new = Path(new_path)
    old = exe.with_name(exe.name + ".old")
    if old.exists():
        try:
            old.unlink()
        except OSError:
            old = exe.with_name(f"{exe.name}.{dt.datetime.now():%H%M%S}.old")
    log(f"교체 시작: {exe.name} ← {new.name}")
    try:
        os.rename(exe, old)
    except OSError as e:
        log(f"실패(지금 exe 이름 바꾸기): {e}")
        raise UpdateError(f"실행 중인 프로그램 이름을 바꾸지 못했습니다({e.__class__.__name__}). 프로그램 폴더에 쓰기 권한이 있는지 확인해 주세요.") from e
    try:
        os.rename(new, exe)
    except OSError as e:
        log(f"실패(새 exe 놓기): {e} — 되돌림")
        try:
            os.rename(old, exe)
        except OSError as e2:
            log(f"되돌리기 실패: {e2}")
        raise UpdateError(f"새 버전으로 바꾸지 못했습니다({e.__class__.__name__}). 다시 시도하거나 릴리스 페이지에서 직접 내려받아 주세요.") from e
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    if not args:
        args = ["--after-update"]
    if not launch:
        log("교체 완료, 다시 시작하면 새 버전 실행")
        return
    if before_launch:
        before_launch()
    try:
        subprocess.Popen([str(exe), *args], cwd=str(exe.parent), env=_clean_env(), creationflags=flags, close_fds=True)
    except OSError as e:
        log(f"새 exe 실행 실패: {e}")
        raise UpdateError(f"새 버전으로 바꿨지만 실행하지 못했습니다. {exe.name}을(를) 직접 실행해 주세요.") from e
    log("교체 완료, 새 버전 실행")


def cleanup_leftovers(wait_seconds: float = 30.0) -> list[str]:
    """새 버전이 켜질 때: 이전 exe(.old)와 남은 .new를 지운다. 이전 앱이 막 끝나는 중일 수 있어 잠시 다시 시도한다."""
    import time

    if not getattr(sys, "frozen", False):
        return []
    exe = current_exe()
    targets = list(exe.parent.glob(exe.name + "*.old")) + [exe.with_name(exe.name + ".new"), app_dir() / (EXE_NAME + ".new")]
    removed = []
    deadline = time.time() + wait_seconds
    pending = [p for p in dict.fromkeys(targets) if p.exists()]
    while pending and time.time() < deadline:
        left = []
        for p in pending:
            try:
                p.unlink()
                removed.append(p.name)
            except FileNotFoundError:
                pass
            except OSError:
                left.append(p)
        pending = left
        if pending:
            time.sleep(0.5)
    if removed:
        log("남은 파일 정리: " + ", ".join(removed))
    return removed
