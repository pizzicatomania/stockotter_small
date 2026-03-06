from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import requests

logger = logging.getLogger(__name__)

_TOKEN_ENDPOINT = "/oauth2/tokenP"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_REFRESH_MARGIN_SECONDS = 60
_ACCOUNT_PATTERN = re.compile(r"^\d{8}-\d{2}$")
_KST = timezone(timedelta(hours=9))

_KIS_BASE_URLS = {
    "paper": "https://openapivts.koreainvestment.com:29443",
    "live": "https://openapi.koreainvestment.com:9443",
}


class TokenCacheCodec(Protocol):
    """Optional cache codec hook for custom encryption/encoding."""

    def encode(self, payload: dict[str, str]) -> str:
        ...

    def decode(self, raw: str) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class KISToken:
    access_token: str
    expires_at: datetime

    def is_expired(
        self,
        *,
        now: datetime,
        refresh_margin_seconds: int = _DEFAULT_REFRESH_MARGIN_SECONDS,
    ) -> bool:
        margin = timedelta(seconds=max(refresh_margin_seconds, 0))
        return now + margin >= self.expires_at


class TokenManager:
    """KIS access token manager with on-disk cache and auto refresh."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        account: str,
        environment: str,
        cache_path: Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        refresh_margin_seconds: int = _DEFAULT_REFRESH_MARGIN_SECONDS,
        session: requests.Session | None = None,
        now_fn: Callable[[], datetime] | None = None,
        cache_codec: TokenCacheCodec | None = None,
    ) -> None:
        normalized_key = app_key.strip()
        normalized_secret = app_secret.strip()
        normalized_account = account.strip()
        if not normalized_key:
            raise ValueError("KIS_APP_KEY must not be empty.")
        if not normalized_secret:
            raise ValueError("KIS_APP_SECRET must not be empty.")
        if not _ACCOUNT_PATTERN.fullmatch(normalized_account):
            raise ValueError("KIS_ACCOUNT must match 8digits-2digits format.")

        normalized_environment = environment.strip().lower()
        self.base_url = resolve_kis_base_url(normalized_environment)

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0.")
        if refresh_margin_seconds < 0:
            raise ValueError("refresh_margin_seconds must be >= 0.")

        self.app_key = normalized_key
        self.app_secret = normalized_secret
        self.account = normalized_account
        self.environment = normalized_environment
        self.cache_path = cache_path or Path(
            f"data/cache/kis/token_{self.environment}.json"
        )
        self.timeout_seconds = timeout_seconds
        self.refresh_margin_seconds = refresh_margin_seconds
        self.session = session or requests.Session()
        self.now_fn = now_fn or _utc_now
        self.cache_codec = cache_codec

    @classmethod
    def from_env(
        cls,
        *,
        cache_path: Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        refresh_margin_seconds: int = _DEFAULT_REFRESH_MARGIN_SECONDS,
        session: requests.Session | None = None,
        now_fn: Callable[[], datetime] | None = None,
        cache_codec: TokenCacheCodec | None = None,
    ) -> TokenManager:
        app_key = os.getenv("KIS_APP_KEY", "").strip()
        app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        account = os.getenv("KIS_ACCOUNT", "").strip()
        environment = os.getenv("KIS_ENV", "paper").strip() or "paper"

        missing = [
            key
            for key, value in {
                "KIS_APP_KEY": app_key,
                "KIS_APP_SECRET": app_secret,
                "KIS_ACCOUNT": account,
            }.items()
            if not value
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required environment variables: {joined}")

        return cls(
            app_key=app_key,
            app_secret=app_secret,
            account=account,
            environment=environment,
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
            refresh_margin_seconds=refresh_margin_seconds,
            session=session,
            now_fn=now_fn,
            cache_codec=cache_codec,
        )

    def get_token(self, *, force_refresh: bool = False) -> KISToken:
        now = self.now_fn()
        if not force_refresh:
            cached = self._load_cached_token()
            if cached is not None and not cached.is_expired(
                now=now,
                refresh_margin_seconds=self.refresh_margin_seconds,
            ):
                logger.info(
                    "kis token cache hit env=%s expires_at=%s",
                    self.environment,
                    cached.expires_at.isoformat(),
                )
                return cached

        fresh = self._fetch_new_token()
        self._save_cached_token(fresh)
        logger.info(
            "kis token refreshed env=%s expires_at=%s",
            self.environment,
            fresh.expires_at.isoformat(),
        )
        return fresh

    def build_bearer_token(self) -> str:
        token = self.get_token()
        return f"Bearer {token.access_token}"

    def _fetch_new_token(self) -> KISToken:
        response = self.session.post(
            f"{self.base_url}{_TOKEN_ENDPOINT}",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid KIS token response payload.")

        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise ValueError("KIS token response missing access_token.")

        expires_at = self._parse_expiry(payload, now=self.now_fn())
        return KISToken(access_token=access_token, expires_at=expires_at)

    def _load_cached_token(self) -> KISToken | None:
        if not self.cache_path.exists():
            return None

        try:
            raw = self.cache_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("failed to read kis token cache path=%s", self.cache_path)
            return None

        try:
            payload = self._decode_cache(raw)
        except ValueError:
            logger.warning("invalid kis token cache payload path=%s", self.cache_path)
            return None

        access_token = str(payload.get("access_token", "")).strip()
        expires_at_raw = str(payload.get("expires_at", "")).strip()
        if not access_token or not expires_at_raw:
            return None

        expires_at = _parse_iso_datetime(expires_at_raw)
        if expires_at is None:
            return None
        return KISToken(access_token=access_token, expires_at=expires_at)

    def _save_cached_token(self, token: KISToken) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "access_token": token.access_token,
            "expires_at": token.expires_at.astimezone(UTC).isoformat(),
            "environment": self.environment,
        }
        encoded = self._encode_cache(payload)

        temp_path = self.cache_path.with_suffix(".tmp")
        temp_path.write_text(encoded, encoding="utf-8")
        temp_path.replace(self.cache_path)

    def _decode_cache(self, raw: str) -> dict[str, Any]:
        if self.cache_codec is None:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("cache payload must be JSON object")
            return payload
        decoded = self.cache_codec.decode(raw)
        if not isinstance(decoded, dict):
            raise ValueError("decoded cache payload must be mapping")
        return decoded

    def _encode_cache(self, payload: dict[str, str]) -> str:
        if self.cache_codec is None:
            return json.dumps(payload, ensure_ascii=False, indent=2)
        return self.cache_codec.encode(payload)

    @staticmethod
    def _parse_expiry(payload: dict[str, Any], *, now: datetime) -> datetime:
        expires_in = payload.get("expires_in")
        if expires_in is not None:
            try:
                seconds = int(float(str(expires_in)))
            except ValueError:
                seconds = 0
            if seconds > 0:
                return now + timedelta(seconds=seconds)

        expires_at_raw = str(payload.get("access_token_token_expired", "")).strip()
        if expires_at_raw:
            parsed = _parse_kis_datetime(expires_at_raw)
            if parsed is not None:
                return parsed

        return now + timedelta(hours=12)


def resolve_kis_base_url(environment: str) -> str:
    normalized = environment.strip().lower()
    if normalized not in _KIS_BASE_URLS:
        raise ValueError("KIS_ENV must be one of: paper, live")
    return _KIS_BASE_URLS[normalized]


def _parse_iso_datetime(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_kis_datetime(raw: str) -> datetime | None:
    try:
        # ISO8601 (with timezone if present)
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = None

    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=_KST)
        return parsed

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=_KST)
        except ValueError:
            continue
    return None


def _utc_now() -> datetime:
    return datetime.now(UTC)
