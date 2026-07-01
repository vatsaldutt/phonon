"""
fan_api.py -- your personal ceiling fan API.

This is the ONLY module your AI assistant should ever import or call.
Everything below it (Home Assistant, ha-ble-adv, the raw BLE protocol
ApplianceSmart uses) is an implementation detail.

Configure via environment variables:
    HA_URL        e.g. http://localhost:8123   (default shown)
    HA_TOKEN      a Home Assistant long-lived access token (required)
    FAN_ENTITY    e.g. fan.ceiling_fan          (default shown)
    LIGHT_ENTITY  e.g. light.ceiling_fan_light
"""

from __future__ import annotations

import os
from typing import Any

import requests

HA_URL = os.environ.get("HA_URL", "http://localhost:8123").rstrip("/")
FAN_ENTITY = os.environ.get("FAN_ENTITY", "fan.vatsal_s_ceiling_fan_main_fan")
LIGHT_ENTITY = os.environ.get("LIGHT_ENTITY", "light.vatsal_s_ceiling_fan_main_light")

# Session is built lazily on first call so that importing this module
# never crashes when HA_TOKEN hasn't been exported yet at import time.
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        token = os.environ.get("HA_TOKEN")
        if not token:
            raise RuntimeError(
                "HA_TOKEN environment variable is not set. "
                "Export it before making fan API calls."
            )
        _session = requests.Session()
        _session.headers.update(
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )
    return _session


def _call_service(domain: str, service: str, **data: Any) -> None:
    url = f"{HA_URL}/api/services/{domain}/{service}"
    resp = _get_session().post(url, json=data, timeout=5)
    resp.raise_for_status()


def _get_state(entity_id: str) -> dict[str, Any]:
    url = f"{HA_URL}/api/states/{entity_id}"
    resp = _get_session().get(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


# ---- Fan -----------------------------------------------------------------

def turn_on() -> None:
    _call_service("fan", "turn_on", entity_id=FAN_ENTITY)


def turn_off() -> None:
    _call_service("fan", "turn_off", entity_id=FAN_ENTITY)


def set_speed(percentage: int) -> None:
    """percentage: 0-100. 0 turns the fan off."""
    percentage = max(0, min(100, percentage))
    _call_service("fan", "set_percentage", entity_id=FAN_ENTITY, percentage=percentage)


def fan_state() -> dict[str, Any]:
    """Raw state + attributes. Check this before deciding what to do."""
    return _get_state(FAN_ENTITY)


# ---- Light ---------------------------------------------------------------

def light_on(
    brightness_pct: int | None = None,
    color_temp_kelvin: int | None = None,
) -> None:
    """
    brightness_pct      : 1-100
    color_temp_kelvin   : 2700 (warm) to 6500 (cool daylight)
    """
    data: dict[str, Any] = {"entity_id": LIGHT_ENTITY}
    if brightness_pct is not None:
        data["brightness_pct"] = max(1, min(100, brightness_pct))
    if color_temp_kelvin is not None:
        data["color_temp_kelvin"] = int(color_temp_kelvin)
    _call_service("light", "turn_on", **data)


def light_off() -> None:
    _call_service("light", "turn_off", entity_id=LIGHT_ENTITY)


def light_state() -> dict[str, Any]:
    return _get_state(LIGHT_ENTITY)


# ---- quick manual test: `python fan_api.py` ------------------------------
if __name__ == "__main__":
    import json
    print("Fan state:")
    print(json.dumps(fan_state(), indent=2))
    print("\nLight state:")
    print(json.dumps(light_state(), indent=2))
