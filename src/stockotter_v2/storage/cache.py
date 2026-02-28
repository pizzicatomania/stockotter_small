from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class FileCache:
    def __init__(self, directory: str | Path, default_ttl_seconds: int | None = None) -> None:
        if default_ttl_seconds is not None and default_ttl_seconds < 0:
            raise ValueError("default_ttl_seconds must be >= 0")

        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.default_ttl_seconds = default_ttl_seconds

    def get(self, key: str, ttl_seconds: int | None = None) -> str | None:
        if ttl_seconds is not None and ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")

        data_path = self._data_path(key)
        if not data_path.exists():
            logger.info("file_cache miss key=%s reason=not_found", self._sha1_key(key))
            return None

        if self._is_expired(key, data_path=data_path, ttl_seconds=ttl_seconds):
            self._delete_paths(key)
            logger.info("file_cache miss key=%s reason=expired", self._sha1_key(key))
            return None

        logger.info("file_cache hit key=%s", self._sha1_key(key))
        return data_path.read_text(encoding="utf-8")

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        if ttl_seconds is not None and ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")

        if ttl_seconds is None:
            ttl_seconds = self.default_ttl_seconds

        data_path = self._data_path(key)
        data_path.write_text(value, encoding="utf-8")

        expire_at = ""
        if ttl_seconds is not None:
            expire_at = str(int(time.time() + ttl_seconds))
        self._meta_path(key).write_text(expire_at, encoding="utf-8")

        logger.info("file_cache set key=%s ttl_seconds=%s", self._sha1_key(key), ttl_seconds)

    @staticmethod
    def _sha1_key(key: str) -> str:
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    def _data_path(self, key: str) -> Path:
        return self.directory / f"{self._sha1_key(key)}.cache"

    def _meta_path(self, key: str) -> Path:
        return self.directory / f"{self._sha1_key(key)}.meta"

    def _delete_paths(self, key: str) -> None:
        for path in [self._data_path(key), self._meta_path(key)]:
            if path.exists():
                path.unlink()

    def _is_expired(self, key: str, data_path: Path, ttl_seconds: int | None) -> bool:
        if ttl_seconds is not None:
            age = time.time() - data_path.stat().st_mtime
            return age > ttl_seconds

        expire_at = self._read_expire_at(key)
        if expire_at is None:
            return False
        return time.time() > expire_at

    def _read_expire_at(self, key: str) -> int | None:
        meta_path = self._meta_path(key)
        if not meta_path.exists():
            return None

        raw = meta_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return int(raw)
