import json
import os
import threading
from typing import Any, Dict, List


_LOCK = threading.Lock()


def _settings_path() -> str:
    """Path to JSON with runtime buy settings.

    Stored outside of *.py so admins can edit/toggle via commands and keep changes across restarts.
    """
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "buy_settings.json")


def _default_settings(tariffs: List[Any] | None = None) -> Dict[str, Any]:
    price = None
    if tariffs:
        try:
            price = int(getattr(tariffs[0], "stars_amount", 0))
        except Exception:
            price = None

    return {
        "enabled": True,
        "price": price if (isinstance(price, int) and price > 0) else 100,
    }


def load_buy_settings(tariffs: List[Any] | None = None) -> Dict[str, Any]:
    path = _settings_path()
    with _LOCK:
        if not os.path.exists(path):
            data = _default_settings(tariffs)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                # If we can't write, still return defaults.
                return data
            return data

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return _default_settings(tariffs)

        enabled = bool(data.get("enabled", True))
        price = data.get("price")
        try:
            price = int(price)
        except Exception:
            price = _default_settings(tariffs)["price"]

        if price <= 0:
            price = _default_settings(tariffs)["price"]

        return {"enabled": enabled, "price": price}


def save_buy_settings(enabled: bool, price: int) -> Dict[str, Any]:
    path = _settings_path()
    data = {"enabled": bool(enabled), "price": int(price)}
    with _LOCK:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def apply_buy_settings(tariffs: List[Any]) -> Dict[str, Any]:
    """Load settings and apply price to tariffs in-place."""
    data = load_buy_settings(tariffs)
    if tariffs:
        try:
            tariffs[0].stars_amount = int(data["price"])
        except Exception:
            pass
    return data


def is_buy_enabled(tariffs: List[Any] | None = None) -> bool:
    return bool(load_buy_settings(tariffs).get("enabled", True))


def set_buy_enabled(enabled: bool, tariffs: List[Any] | None = None) -> Dict[str, Any]:
    current = load_buy_settings(tariffs)
    return save_buy_settings(enabled=enabled, price=int(current["price"]))


def set_buy_price(price: int, tariffs: List[Any] | None = None) -> Dict[str, Any]:
    current = load_buy_settings(tariffs)
    return save_buy_settings(enabled=bool(current["enabled"]), price=int(price))
