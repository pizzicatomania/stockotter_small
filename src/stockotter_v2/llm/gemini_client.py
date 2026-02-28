from __future__ import annotations

import os
from typing import Any

import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiClient:
    """Minimal Gemini generateContent client for JSON extraction."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        timeout_seconds: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Gemini API key is empty.")
        if not model.strip():
            raise ValueError("Gemini model is empty.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0.")

        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @classmethod
    def from_env(
        cls,
        *,
        model: str,
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
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            session=session,
        )

    def generate(self, prompt: str) -> str:
        response = self.session.post(
            f"{GEMINI_API_BASE}/models/{self.model}:generateContent",
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
