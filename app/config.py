import json
import threading

from .paths import data_dir
from .prompts import DEFAULT_REFINE_PROMPTS, DEFAULT_TRANSFORM_TABS

CONFIG_NAME = "악필교정기_설정.json"
KEY_FIELDS = ("clova_url", "clova_secret", "openrouter_key")

DEFAULTS = {
    "clova_url": "",
    "clova_secret": "",
    "openrouter_key": "",
    "ocr_lang": "ko",
    "refine_model": "",
    "transform_model": "",
    "refine_mode": "B",
    "refine_prompts": DEFAULT_REFINE_PROMPTS,
    "transform_tabs": DEFAULT_TRANSFORM_TABS,
    "active_tab": 0,
    "target_bytes": 1500,
    "pages_per_student": 1,
    "layout_mode": "auto",  # 2단 나누기: auto(자동) / off(나누지 않음) / two(2단으로)
    "skip_group_check": False,  # OCR 뒤 묶기 확인 없이 바로 정제
    "read_side": "both",  # 읽을 단: both / left / right
    "ocr_engine": "clova",  # 읽는 방식: clova / ai(Qwen만) / hybrid
    "ocr_ai_model": "qwen/qwen3-vl-235b-a22b-instruct",
    "ocr_fix_model": "",  # 하이브리드 [AI 제안] 모델(비우면 정제 모델)
    "ocr_mask": 0.12,  # Qwen에 보낼 때 위쪽 가림 비율
    "ocr_ai_consent": False,  # 가림 0%로 Qwen에 보내는 데 동의
}

_lock = threading.Lock()


def config_path():
    return data_dir() / CONFIG_NAME


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(config_path(), encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    tabs = cfg.get("transform_tabs")
    if not isinstance(tabs, list) or len(tabs) != 5:
        cfg["transform_tabs"] = DEFAULT_TRANSFORM_TABS
    if not isinstance(cfg.get("refine_prompts"), dict):
        cfg["refine_prompts"] = DEFAULT_REFINE_PROMPTS
    if cfg.get("refine_mode") not in ("A", "B"):
        cfg["refine_mode"] = "B"
    if cfg.get("read_side") not in ("both", "left", "right"):
        cfg["read_side"] = "both"
    if cfg.get("ocr_engine") not in ("clova", "ai", "hybrid"):
        cfg["ocr_engine"] = "clova"
    from .ocr_vision import MODELS
    if cfg.get("ocr_ai_model") not in [m for m, _ in MODELS]:
        cfg["ocr_ai_model"] = MODELS[0][0]
    cfg["ocr_fix_model"] = str(cfg.get("ocr_fix_model") or "").strip()
    try:
        cfg["ocr_mask"] = min(0.4, max(0.0, round(float(cfg.get("ocr_mask")), 3)))
    except (TypeError, ValueError):
        cfg["ocr_mask"] = 0.12
    cfg["ocr_ai_consent"] = cfg.get("ocr_ai_consent") is True
    if cfg.get("layout_mode") not in ("auto", "off", "two"):
        cfg["layout_mode"] = "auto"
    if not isinstance(cfg.get("active_tab"), int) or not 0 <= cfg["active_tab"] < 5:
        cfg["active_tab"] = 0
    return cfg


def save(cfg: dict) -> dict:
    with _lock:
        merged = load()
        merged.update({k: v for k, v in cfg.items() if k in DEFAULTS})
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        return merged


def reset_keys() -> dict:
    return save({k: "" for k in KEY_FIELDS})
