"""Offline license verification and trial state.

A license is a compact signed token::

    base64url(canonical-json-payload) + "." + base64url(ed25519-signature)

The private key never ships.  Release engineering keeps it offline and uses
``scripts/license_tool.py`` to issue customer tokens.  Only the public key is
embedded below.  Verification is offline and requires no activation server.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import platform
import uuid
from pathlib import Path

PRODUCT = "meetingVAR"
TRIAL_DAYS = 14
# Release public key (Ed25519 raw, hex). Override only for local tests.
_PUBLIC_KEY_HEX = "f0d4621bfea8403d915d40e102da6c759f2850ff80e6c9acef75ff783e761444"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def machine_id() -> str:
    """Stable machine fingerprint without collecting personal data."""
    parts = [platform.node() or "", platform.machine() or "", str(uuid.getnode())]
    try:
        if os.name == "nt":
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                parts.append(str(winreg.QueryValueEx(key, "MachineGuid")[0]))
    except (ImportError, OSError):
        pass
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _public_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key_hex = os.environ.get("MEETINGVAR_LICENSE_PUBLIC_KEY", _PUBLIC_KEY_HEX).strip()
    try:
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
    except (ValueError, TypeError):
        raise ValueError("license public key is invalid") from None


def issue_license(private_key_pem: bytes, *, issued_to: str, edition: str = "professional",
                  seats: int = 1, expires: str = "", machine: str = "",
                  features: list[str] | None = None) -> str:
    """Issue a token.  Used by the offline release tool, never by the app."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    payload = {
        "product": PRODUCT, "issued_to": str(issued_to).strip(),
        "edition": str(edition).strip() or "professional",
        "seats": max(1, int(seats)), "expires": str(expires).strip(),
        "machine": str(machine).strip(), "features": list(features or []),
        "issued_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    signature = key.sign(canonical)
    return _b64e(canonical) + "." + _b64e(signature)


def verify_license(token: str, machine: str | None = None) -> dict:
    """Return a verified license payload or ``{"valid": False, "reason": ...}``."""
    token = str(token or "").strip()
    if not token:
        return {"valid": False, "reason": "未激活"}
    try:
        payload_part, signature_part = token.split(".", 1)
        raw = _b64d(payload_part)
        signature = _b64d(signature_part)
        _public_key().verify(signature, raw)
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"valid": False, "reason": "许可证签名无效"}
    if not isinstance(payload, dict) or payload.get("product") != PRODUCT:
        return {"valid": False, "reason": "许可证不属于本产品"}
    if payload.get("expires"):
        try:
            expires = dt.date.fromisoformat(str(payload["expires"]))
        except ValueError:
            return {"valid": False, "reason": "许可证到期日无效"}
        if dt.date.today() > expires:
            return {"valid": False, "reason": "许可证已到期", "expires": str(expires)}
    bound = str(payload.get("machine") or "").strip()
    if bound and bound != (machine or machine_id()):
        return {"valid": False, "reason": "许可证与本机不匹配"}
    try:
        seats = int(payload.get("seats", 1))
    except (TypeError, ValueError):
        seats = 1
    payload.update({"valid": True, "seats": max(1, seats), "machine": bound,
                    "machine_id": machine or machine_id()})
    return payload


def trial_status(started: str, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    try:
        start = dt.date.fromisoformat(str(started))
    except (TypeError, ValueError):
        start = today
    used = max(0, (today - start).days)
    left = max(0, TRIAL_DAYS - used)
    return {"started": start.isoformat(), "used_days": used,
            "days_left": left, "expired": left <= 0}


def status(token: str, trial_started: str) -> dict:
    verified = verify_license(token) if token else {"valid": False, "reason": "未激活"}
    if verified.get("valid"):
        return {"state": "licensed", "license": verified,
                "edition": verified.get("edition", "professional"),
                "issued_to": verified.get("issued_to", ""),
                "expires": verified.get("expires", ""),
                "features": verified.get("features", []),
                "trial": trial_status(trial_started)}
    trial = trial_status(trial_started)
    return {"state": "trial" if not trial["expired"] else "expired",
            "license": verified, "edition": "trial",
            "issued_to": "", "expires": "", "features": [],
            "trial": trial, "reason": verified.get("reason", "")}