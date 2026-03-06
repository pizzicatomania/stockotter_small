from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from stockotter_small.broker.kis.schemas import (
    KISAccountBalance,
    KISPosition,
    KISPriceQuote,
)
from stockotter_small.broker.kis.token_manager import TokenManager, split_kis_account

_AUTH_TEST_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"

_PRICE_TR_ID = "FHKST01010100"
_BALANCE_TR_ID = {
    "paper": "VTTC8434R",
    "live": "TTTC8434R",
}


@dataclass(frozen=True)
class AuthTestResult:
    status_code: int
    output_code: str | None
    output_message: str | None
    stock_name: str | None
    current_price: str | None


class KISClientError(RuntimeError):
    """Base error for KIS client failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        msg_cd: str | None = None,
        rt_cd: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.msg_cd = msg_cd
        self.rt_cd = rt_cd


class KISAuthError(KISClientError):
    """KIS authentication/authorization failure."""


class KISRateLimitError(KISClientError):
    """KIS rate limit failure."""


class KISAPIError(KISClientError):
    """KIS API response failure."""


class KISClient:
    """KIS API client for quote/account inquiry use-cases."""

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
        self._cano, self._acnt_prdt_cd = split_kis_account(token_manager.account)

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
        ticker_code = _normalize_ticker(ticker)

        payload, response = self._request_get(
            path=_AUTH_TEST_PATH,
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker_code,
            },
            tr_id=_PRICE_TR_ID,
        )

        output = payload.get("output")
        output_data = output if isinstance(output, dict) else {}

        return AuthTestResult(
            status_code=response.status_code,
            output_code=_as_optional_string(payload.get("rt_cd")),
            output_message=_as_optional_string(payload.get("msg1")),
            stock_name=_as_optional_string(output_data.get("hts_kor_isnm")),
            current_price=_as_optional_string(output_data.get("stck_prpr")),
        )

    def get_price(self, ticker: str) -> KISPriceQuote:
        ticker_code = _normalize_ticker(ticker)

        payload, _ = self._request_get(
            path=_AUTH_TEST_PATH,
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker_code,
            },
            tr_id=_PRICE_TR_ID,
        )
        output = payload.get("output")
        if not isinstance(output, dict):
            raise KISAPIError("KIS price response missing output payload")

        return KISPriceQuote.model_validate(
            {
                "ticker": ticker_code,
                "name": output.get("hts_kor_isnm") or output.get("prdt_name") or ticker_code,
                "current_price": output.get("stck_prpr"),
                "previous_close": output.get("stck_sdpr"),
                "change": output.get("prdy_vrss"),
                "change_rate": output.get("prdy_ctrt"),
            }
        )

    def get_balance(self) -> KISAccountBalance:
        payload, _ = self._request_get(
            path=_BALANCE_PATH,
            params=self._build_balance_query(),
            tr_id=self._balance_tr_id,
        )
        summary = _first_row(payload.get("output2"))
        if summary is None:
            raise KISAPIError("KIS balance response missing output2 payload")

        return KISAccountBalance.model_validate(
            {
                "total_purchase_amount": _pick(summary, "pchs_amt_smtl_amt", "pchs_amt_smtl"),
                "total_eval_amount": _pick(summary, "tot_evlu_amt", "evlu_amt_smtl_amt"),
                "total_profit_loss_amount": _pick(
                    summary,
                    "evlu_pfls_smtl_amt",
                    "tot_evlu_pfls_amt",
                ),
                "total_profit_loss_rate": _pick(
                    summary,
                    "tot_evlu_pfls_rt",
                    "evlu_erng_rt",
                    "asst_icdc_erng_rt",
                    "asst_icdc_amt",
                    default="0",
                ),
                "cash_available": _pick(
                    summary,
                    "dnca_tot_amt",
                    "tot_dncl_amt",
                    "nass_amt",
                    default=None,
                ),
            }
        )

    def get_positions(self) -> list[KISPosition]:
        payload, _ = self._request_get(
            path=_BALANCE_PATH,
            params=self._build_balance_query(),
            tr_id=self._balance_tr_id,
        )

        rows = payload.get("output1")
        if not isinstance(rows, list):
            raise KISAPIError("KIS positions response missing output1 payload")

        positions: list[KISPosition] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(_pick(row, "pdno", "mksc_shrn_iscd", default="")).strip()
            if not ticker:
                continue
            positions.append(
                KISPosition.model_validate(
                    {
                        "ticker": ticker,
                        "name": _pick(row, "prdt_name", "hldg_name", default=ticker),
                        "quantity": _pick(row, "hldg_qty", "hold_qty", default="0"),
                        "orderable_quantity": _pick(
                            row,
                            "ord_psbl_qty",
                            "ord_psbl_qty1",
                            default=None,
                        ),
                        "average_buy_price": _pick(
                            row,
                            "pchs_avg_pric",
                            "pchs_avg_prc",
                            default=None,
                        ),
                        "current_price": _pick(row, "prpr", "stck_prpr", default=None),
                        "eval_amount": _pick(row, "evlu_amt", default=None),
                        "profit_loss_amount": _pick(row, "evlu_pfls_amt", default=None),
                        "profit_loss_rate": _pick(row, "evlu_pfls_rt", default=None),
                    }
                )
            )
        return positions

    @property
    def _balance_tr_id(self) -> str:
        tr_id = _BALANCE_TR_ID.get(self.environment)
        if tr_id is None:
            raise ValueError(f"unsupported KIS environment for balance tr_id: {self.environment}")
        return tr_id

    def _request_get(
        self,
        *,
        path: str,
        params: dict[str, str],
        tr_id: str,
    ) -> tuple[dict[str, Any], requests.Response]:
        try:
            bearer_token = self.token_manager.build_bearer_token()
        except ValueError as exc:
            raise KISAuthError(f"KIS token error msg={exc}") from exc

        try:
            response = self.session.get(
                f"{self.token_manager.base_url}{path}",
                params=params,
                headers={
                    "authorization": bearer_token,
                    "appkey": self.token_manager.app_key,
                    "appsecret": self.token_manager.app_secret,
                    "tr_id": tr_id,
                    "custtype": "P",
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise KISAPIError(
                f"KIS request transport error type={exc.__class__.__name__}"
            ) from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise _to_kis_http_error(response=response) from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise KISAPIError("KIS API returned non-object payload")

        rt_cd = _as_optional_string(payload.get("rt_cd"))
        if rt_cd not in {None, "0"}:
            raise _to_kis_business_error(payload=payload, rt_cd=rt_cd)

        return payload, response

    def _build_balance_query(
        self,
        *,
        ctx_area_fk100: str = "",
        ctx_area_nk100: str = "",
    ) -> dict[str, str]:
        return {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": ctx_area_fk100,
            "CTX_AREA_NK100": ctx_area_nk100,
        }


def _to_kis_http_error(*, response: requests.Response) -> KISClientError:
    status = response.status_code
    payload = _parse_error_payload(response=response)
    msg_cd = _as_optional_string(payload.get("msg_cd")) or "-"
    msg = _as_optional_string(payload.get("msg1")) or _as_optional_string(payload.get("message"))
    detail = msg or "request failed"

    if status == 429:
        return KISRateLimitError(
            f"KIS rate limit status=429 msg_cd={msg_cd} msg={detail}",
            status_code=status,
            msg_cd=msg_cd,
        )
    if status in {401, 403}:
        return KISAuthError(
            f"KIS auth error status={status} msg_cd={msg_cd} msg={detail}",
            status_code=status,
            msg_cd=msg_cd,
        )
    return KISAPIError(
        f"KIS HTTP error status={status} msg_cd={msg_cd} msg={detail}",
        status_code=status,
        msg_cd=msg_cd,
    )


def _to_kis_business_error(*, payload: dict[str, Any], rt_cd: str) -> KISClientError:
    msg_cd = _as_optional_string(payload.get("msg_cd")) or "-"
    msg = _as_optional_string(payload.get("msg1")) or "unknown error"
    normalized_msg = msg.lower()

    if _is_rate_limit_business_error(msg_cd=msg_cd, message=normalized_msg):
        return KISRateLimitError(
            f"KIS API business error rt_cd={rt_cd} msg_cd={msg_cd} msg={msg}",
            msg_cd=msg_cd,
            rt_cd=rt_cd,
        )
    if _is_auth_business_error(msg_cd=msg_cd, message=normalized_msg):
        return KISAuthError(
            f"KIS API business error rt_cd={rt_cd} msg_cd={msg_cd} msg={msg}",
            msg_cd=msg_cd,
            rt_cd=rt_cd,
        )
    return KISAPIError(
        f"KIS API business error rt_cd={rt_cd} msg_cd={msg_cd} msg={msg}",
        msg_cd=msg_cd,
        rt_cd=rt_cd,
    )


def _is_rate_limit_business_error(*, msg_cd: str, message: str) -> bool:
    if msg_cd in {"EGW00123", "EGW00201", "EGW00202"}:
        return True
    keywords = (
        "too many",
        "rate limit",
        "quota",
        "request limit",
        "호출 횟수",
        "요청 한도",
        "호출량",
        "초과",
    )
    return any(keyword in message for keyword in keywords)


def _is_auth_business_error(*, msg_cd: str, message: str) -> bool:
    if msg_cd in {"EGW00001", "EGW00121", "EGW00122"}:
        return True
    keywords = (
        "unauthorized",
        "forbidden",
        "invalid token",
        "token invalid",
        "token",
        "access",
        "인증",
        "권한",
        "토큰",
        "접근",
    )
    return any(keyword in message for keyword in keywords)


def _parse_error_payload(*, response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _normalize_ticker(ticker: str) -> str:
    ticker_code = ticker.strip()
    if not ticker_code:
        raise ValueError("ticker must not be empty")
    if not ticker_code.isdigit():
        raise ValueError("ticker must be numeric")
    if len(ticker_code) not in {5, 6}:
        raise ValueError("ticker must be 5 or 6 digits")
    return ticker_code.zfill(6)


def _first_row(value: object) -> dict[str, Any] | None:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return first
        return None
    if isinstance(value, dict):
        return value
    return None


def _pick(payload: dict[str, Any], *keys: str, default: object | None = "") -> object | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        return value
    return default


def _as_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text
