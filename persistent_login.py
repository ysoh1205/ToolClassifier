"""Serialization and expiry rules for persistent Supabase login sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any


COOKIE_NAME = "supabase_session"
COOKIE_PREFIX = "mcp-tool-catalog/"
LOGIN_DURATION = timedelta(days=30)
PAYLOAD_VERSION = 1


@dataclass(frozen=True)
class PersistentSession:
    access_token: str
    refresh_token: str
    expires_at: int


def _utc_timestamp(now: datetime | None = None) -> int:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def create_persistent_session(
    access_token: str,
    refresh_token: str,
    *,
    now: datetime | None = None,
) -> PersistentSession:
    """Create a session with an absolute 30-day lifetime."""
    if not access_token or not refresh_token:
        raise ValueError("Both Supabase session tokens are required.")
    return PersistentSession(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=_utc_timestamp(now) + int(LOGIN_DURATION.total_seconds()),
    )


def serialize_persistent_session(session: PersistentSession) -> str:
    return json.dumps(
        {
            "version": PAYLOAD_VERSION,
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "expires_at": session.expires_at,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def deserialize_persistent_session(
    raw_value: Any,
    *,
    now: datetime | None = None,
) -> PersistentSession | None:
    """Return a valid, unexpired session or ``None`` for unsafe input."""
    if not isinstance(raw_value, str) or not raw_value:
        return None
    try:
        payload = json.loads(raw_value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != PAYLOAD_VERSION:
        return None

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_at = payload.get("expires_at")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(refresh_token, str)
        or not refresh_token
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= _utc_timestamp(now)
    ):
        return None
    return PersistentSession(access_token, refresh_token, expires_at)


def set_cookie_expiry(
    cookie_manager: Any,
    *,
    expires_at: int | None = None,
    now: datetime | None = None,
) -> None:
    """Set the pinned cookie manager's browser expiry to 30 days.

    The package does not expose this setting publicly. The application payload
    independently enforces the same absolute expiry, so a package regression
    cannot extend the authenticated session.
    """
    underlying_manager = getattr(cookie_manager, "_cookie_manager", None)
    if underlying_manager is None or not hasattr(
        underlying_manager, "_default_expiry"
    ):
        raise RuntimeError("Unsupported streamlit-cookies-manager-v2 version.")

    if expires_at is not None:
        expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc)
    else:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        expiry = moment.astimezone(timezone.utc) + LOGIN_DURATION
    underlying_manager._default_expiry = expiry
