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
