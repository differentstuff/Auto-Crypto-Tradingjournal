"""
routes/settings.py — Exchange credential management and connection status.

GET  /api/settings/exchanges         — configured exchanges + connection status
POST /api/settings/test-connection   — test Bitget or Blofin connection live
POST /api/settings/credentials       — save new credentials to .env
GET  /api/settings/blofin/sync       — manual Blofin sync trigger
GET  /api/settings/blofin/status     — Blofin sync status
"""

import os
import re

from flask import Blueprint, request

import bitget_client
import blofin_client
import blofin_sync
from helpers import _ok, _err

bp = Blueprint("settings", __name__)

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


def _mask(value: str) -> str:
    """Show first 4 + last 4 chars, rest as *."""
    if not value or len(value) < 8:
        return "****"
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def _read_env() -> dict:
    """Parse .env into a dict. Returns empty dict if file missing."""
    result = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    result[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return result


def _write_env(updates: dict):
    """
    Update .env file in place, adding or overwriting specified keys.
    Creates the file if it doesn't exist.
    """
    existing = {}
    lines    = []

    try:
        with open(ENV_PATH) as f:
            raw = f.readlines()
        for line in raw:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, v = stripped.partition("=")
                existing[k.strip()] = len(lines)
            lines.append(line)
    except FileNotFoundError:
        raw   = []
        lines = []

    for key, val in updates.items():
        if key in existing:
            # Replace the existing line
            idx = existing[key]
            lines[idx] = f"{key}={val}\n"
        else:
            # Append at end
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.append(f"{key}={val}\n")

    with open(ENV_PATH, "w") as f:
        f.writelines(lines)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@bp.get("/api/settings/exchanges")
def exchanges_status():
    """Return configured status for each exchange (never returns raw secrets)."""
    env = _read_env()

    bitget_key = os.environ.get("BITGET_API_KEY") or env.get("BITGET_API_KEY", "")
    bitget_sec = os.environ.get("BITGET_SECRET_KEY") or env.get("BITGET_SECRET_KEY", "")
    bitget_pp  = os.environ.get("BITGET_PASSPHRASE") or env.get("BITGET_PASSPHRASE", "")

    blofin_key = os.environ.get("BLOFIN_API_KEY") or env.get("BLOFIN_API_KEY", "")
    blofin_sec = os.environ.get("BLOFIN_SECRET_KEY") or env.get("BLOFIN_SECRET_KEY", "")
    blofin_pp  = os.environ.get("BLOFIN_PASSPHRASE") or env.get("BLOFIN_PASSPHRASE", "")

    return _ok({
        "bitget": {
            "configured": bool(bitget_key and bitget_sec),
            "api_key_preview":  _mask(bitget_key)  if bitget_key  else "",
            "has_passphrase": bool(bitget_pp),
        },
        "blofin": {
            "configured": bool(blofin_key and blofin_sec),
            "api_key_preview":  _mask(blofin_key)  if blofin_key  else "",
            "has_passphrase": bool(blofin_pp),
        },
        "env_file_exists": os.path.exists(ENV_PATH),
    })


@bp.post("/api/settings/test-connection")
def test_connection():
    """Test Bitget or Blofin connection live. Body: {exchange: 'bitget'|'blofin'}"""
    data     = request.get_json(silent=True) or {}
    exchange = (data.get("exchange") or "bitget").lower()

    if exchange == "blofin":
        result = blofin_client.test_connection()
        if result.get("ok"):
            return _ok({"exchange": exchange, "message": result["msg"]})
        return _err(f"Blofin: {result.get('msg', 'Connection failed')}")
    else:
        try:
            equity = bitget_client.test_connection()
            if equity and equity.get("equity") is not None:
                return _ok({"exchange": "bitget", "message": f"Connected — equity {equity['equity']} USDT"})
            return _err("Bitget: no equity data returned — check credentials")
        except Exception:
            return _err("Bitget connection failed — check credentials and API permissions")


@bp.post("/api/settings/credentials")
def save_credentials():
    """
    Write new credentials to .env.
    Body: {exchange, api_key, secret_key, passphrase}
    Reloads env vars in the current process so the change takes effect immediately.
    """
    data     = request.get_json(silent=True) or {}
    exchange = (data.get("exchange") or "").lower()
    api_key  = (data.get("api_key")    or "").strip()
    secret   = (data.get("secret_key") or "").strip()
    phrase   = (data.get("passphrase") or "").strip().replace("\n", "").replace("\r", "")

    if exchange not in ("bitget", "blofin"):
        return _err("exchange must be 'bitget' or 'blofin'")
    if not api_key or not secret:
        return _err("api_key and secret_key are required")

    # Basic sanity: only allow hex/alphanumeric API keys
    if not re.match(r'^[A-Za-z0-9\-_]+$', api_key) or not re.match(r'^[A-Za-z0-9\-_]+$', secret):
        return _err("Credentials contain invalid characters")

    prefix = exchange.upper()
    updates = {
        f"{prefix}_API_KEY":    api_key,
        f"{prefix}_SECRET_KEY": secret,
        f"{prefix}_PASSPHRASE": phrase,
    }
    _write_env(updates)

    # Reload into current process
    for k, v in updates.items():
        os.environ[k] = v

    # Reinitialise the relevant client module constants
    if exchange == "blofin":
        blofin_client.API_KEY    = api_key
        blofin_client.SECRET_KEY = secret
        blofin_client.PASSPHRASE = phrase
    else:
        bitget_client.API_KEY    = api_key
        bitget_client.SECRET_KEY = secret
        bitget_client.PASSPHRASE = phrase

    return _ok({"exchange": exchange, "saved": True})


@bp.post("/api/settings/blofin/sync")
def blofin_sync_trigger():
    """Trigger a manual Blofin sync."""
    if not blofin_client.is_configured():
        return _err("Blofin credentials not configured")
    try:
        result = blofin_sync.run_sync()
        return _ok(result)
    except Exception:
        return _err("Blofin sync failed — check server logs", 500)


@bp.get("/api/settings/blofin/status")
def blofin_sync_status():
    """Return Blofin sync status."""
    from database import db_conn
    status = blofin_sync.get_status()
    with db_conn() as conn:
        equity    = conn.execute("SELECT value FROM settings WHERE key='blofin_equity'").fetchone()
        available = conn.execute("SELECT value FROM settings WHERE key='blofin_available'").fetchone()
        last_sync = conn.execute("SELECT value FROM settings WHERE key='blofin_last_sync_ms'").fetchone()
    status["account_equity"]    = float(equity[0])    if equity    else None
    status["available_balance"] = float(available[0]) if available else None
    status["last_sync_ms"]      = int(last_sync[0])   if last_sync  else None
    return _ok(status)
