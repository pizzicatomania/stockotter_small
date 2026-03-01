from __future__ import annotations

import logging
import os
from typing import Any

import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_QUOTA_STATUS_CODES = {403, 429}

logger = logging.getLogger(__name__)


class GeminiClient:
    """Minimal Gemini generateContent client for JSON extraction."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        fallback_model: str | None = "gemini-2.5-flash-lite",
        temperature: float = 0.0,
        timeout_seconds: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Gemini API key is empty.")
        if not model.strip():
            raise ValueError("Gemini model is empty.")
        if fallback_model is not None and not fallback_model.strip():
            raise ValueError("Gemini fallback model is empty.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0.")

        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @classmethod
    def from_env(
        cls,
        *,
        model: str,
        fallback_model: str | None = "gemini-2.5-flash-lite",
        temperature: float = 0.0,
        timeout_seconds: float = 20.0,
        env_var: str = "GEMINI_API_KEY",
        session: requests.Session | None = None,
    ) -> GeminiClient:
        api_key = os.getenv(env_var, "").strip()
        if not api_key:
            raise ValueError(f"Environment variable {env_var} is not set.")
        return cls(
            api_key=api_key,
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            session=session,
        )

    def generate(self, prompt: str) -> str:
        try:
            return self._generate_with_model(prompt=prompt, model=self.model)
        except requests.HTTPError as exc:
            if not self._should_fallback(exc):
                raise
            logger.warning(
                "gemini quota exhausted: primary model=%s, fallback model=%s",
                self.model,
                self.fallback_model,
            )
            return self._generate_with_model(
                prompt=prompt,
                model=self.fallback_model or self.model,
            )

    def _generate_with_model(self, *, prompt: str, model: str) -> str:
        response = self.session.post(
            f"{GEMINI_API_BASE}/models/{model}:generateContent",
            params={"key": self.api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": self.temperature,
                    "responseMimeType": "application/json",
                },
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return self._extract_text(payload)

    def _should_fallback(self, error: requests.HTTPError) -> bool:
        if not self.fallback_model:
            return False
        if self.fallback_model == self.model:
            return False

        response = error.response
        if response is None:
            return False

        if response.status_code not in _QUOTA_STATUS_CODES:
            return False

        error_payload = self._extract_error_payload(response=response)
        status = str(error_payload.get("status", "")).upper()
        message = str(error_payload.get("message", "")).lower()
        reason = str(error_payload.get("reason", "")).lower()
        text = response.text.lower()

        tokens = (
            "resource_exhausted",
            "quota",
            "rate limit",
            "too many requests",
            "exceeded",
        )
        return (
            status == "RESOURCE_EXHAUSTED"
            or any(token in message for token in tokens)
            or any(token in reason for token in tokens)
            or any(token in text for token in tokens)
        )

    @staticmethod
    def _extract_error_payload(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        if not isinstance(payload, dict):
            return {}
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            return error_payload
        return payload

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Gemini response has no candidates.")

        first = candidates[0]
        if not isinstance(first, dict):
            raise ValueError("Gemini response candidate payload is invalid.")

        content = first.get("content")
        if not isinstance(content, dict):
            raise ValueError("Gemini response content is missing.")

        parts = content.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("Gemini response has no content parts.")

        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text

        raise ValueError("Gemini response has no text output.")
