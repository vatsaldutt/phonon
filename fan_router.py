"""
fan_router.py — FastAPI router for ceiling fan control.

Mounted at /fan by main.py. All endpoints proxy through fan_api.py
which talks to Home Assistant over HTTP.

Fan endpoints:
    GET  /fan/state              current fan state + attributes
    POST /fan/on                 turn fan on
    POST /fan/off                turn fan off
    POST /fan/speed              {"percentage": 0-100}

Light endpoints:
    GET  /fan/light/state        current light state + attributes
    POST /fan/light/on           {"brightness_pct": 1-100, "color_temp_kelvin": 2700-6500}
    POST /fan/light/off          turn light off
"""

from __future__ import annotations

from typing import Optional

import fan_api
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/fan", tags=["Fan Control"])


def _ha_error(exc: Exception) -> HTTPException:
    """Translate fan_api / requests exceptions into sensible HTTP responses."""
    msg = str(exc)
    if "HA_TOKEN" in msg:
        return HTTPException(503, "HA_TOKEN not configured on the server")
    if "Connection" in msg or "timed out" in msg.lower():
        return HTTPException(503, "Cannot reach Home Assistant — is hass running?")
    return HTTPException(502, f"Home Assistant error: {msg}")


# ── Fan ───────────────────────────────────────────────────────────────────────

@router.get("/state")
def fan_state():
    """Return the raw HA state dict for the fan entity."""
    try:
        return fan_api.fan_state()
    except Exception as e:
        raise _ha_error(e)


@router.post("/on")
def fan_on():
    try:
        fan_api.turn_on()
        return {"status": "on"}
    except Exception as e:
        raise _ha_error(e)


@router.post("/off")
def fan_off():
    try:
        fan_api.turn_off()
        return {"status": "off"}
    except Exception as e:
        raise _ha_error(e)


class SpeedIn(BaseModel):
    percentage: int = Field(..., ge=0, le=100, description="Fan speed 0–100 %")


@router.post("/speed")
def fan_speed(body: SpeedIn):
    try:
        fan_api.set_speed(body.percentage)
        return {"status": "ok", "percentage": body.percentage}
    except Exception as e:
        raise _ha_error(e)


# ── Light ─────────────────────────────────────────────────────────────────────

@router.get("/light/state")
def light_state():
    """Return the raw HA state dict for the light entity."""
    try:
        return fan_api.light_state()
    except Exception as e:
        raise _ha_error(e)


class LightIn(BaseModel):
    brightness_pct: Optional[int] = Field(
        None, ge=1, le=100, description="Brightness 1–100 %"
    )
    color_temp_kelvin: Optional[int] = Field(
        None, ge=2700, le=6500, description="Colour temperature in Kelvin"
    )


@router.post("/light/on")
def light_on(body: LightIn = LightIn()):
    """
    Turn the light on. Both fields are optional — omit to keep current values.

    Examples:
        {}                                     → on at current brightness/temp
        {"brightness_pct": 80}                 → 80 % brightness
        {"color_temp_kelvin": 2700}            → warm white
        {"brightness_pct": 50, "color_temp_kelvin": 5000}  → both
    """
    try:
        fan_api.light_on(
            brightness_pct=body.brightness_pct,
            color_temp_kelvin=body.color_temp_kelvin,
        )
        return {"status": "on", "brightness_pct": body.brightness_pct,
                "color_temp_kelvin": body.color_temp_kelvin}
    except Exception as e:
        raise _ha_error(e)


@router.post("/light/off")
def light_off():
    try:
        fan_api.light_off()
        return {"status": "off"}
    except Exception as e:
        raise _ha_error(e)