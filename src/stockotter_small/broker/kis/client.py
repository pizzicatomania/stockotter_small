from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests

from stockotter_small.broker.kis.token_manager import TokenManager

_AUTH_TEST_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"


@dataclass(frozen=True)
class AuthTestResult:
    status_code: int
    output_code: str | None
    output_message: str | None
    stock_name: str | None
    current_price: str | None


class KISClient:
    """Minimal KIS API client for auth/token validation use-cases."""

    def __init__(
        self,
        *,
        token_manager: TokenManager,
        timeout_seconds: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0.")
        self.token_manager = token_manager
        self.timeout_seconds = timeout_seconds
        self.session = session or token_manager.session

    @classmethod
    def from_env(
        cls,
        *,
        cache_path: Path | None = None,
        timeout_seconds: float = 10.0,
        refresh_margin_seconds: int = 60,
        session: requests.Session | None = None,
    ) -> KISClient:
        token_manager = TokenManager.from_env(
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
            refresh_margin_seconds=refresh_margin_seconds,
            session=session,
        )
        return cls(
            token_manager=token_manager,
            timeout_seconds=timeout_seconds,
            session=session,
        )

    @property
    def environment(self) -> str:
        return self.token_manager.environment

    @property
    def cache_path(self) -> Path:
        return self.token_manager.cache_path

    def auth_test_quote(self, *, ticker: str = "005930") -> AuthTestResult:
        ticker_code = ticker.strip()
        if not ticker_code:
            raise ValueError("ticker must not be empty")

        response = self.session.get(
            f"{self.token_manager.base_url}{_AUTH_TEST_PATH}",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker_code,
            },
            headers={
                "authorization": self.token_manager.build_bearer_token(),
                "appkey": self.token_manager.app_key,
                "appsecret": self.token_manager.app_secret,
                "tr_id": "FHKST01010100",
                "custtype": "P",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid KIS quote response payload.")

        output = payload.get("output")
        output_data = output if isinstance(output, dict) else {}

        return AuthTestResult(
            status_code=response.status_code,
            output_code=_as_optional_string(payload.get("rt_cd")),
            output_message=_as_optional_string(payload.get("msg1")),
            stock_name=_as_optional_string(output_data.get("hts_kor_isnm")),
            current_price=_as_optional_string(output_data.get("stck_prpr")),
        )


def _as_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text
