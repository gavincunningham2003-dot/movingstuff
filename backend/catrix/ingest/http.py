"""HTTP download client with on-disk caching, retries and checksum recording.

Design notes
------------
* Every download is cached under ``data/cache/<sha1(url)>`` with a sidecar ``.meta.json``
  (URL, retrieval timestamp, size, sha256, content-type). Re-running the pipeline reuses
  the cache unless ``force=True``.
* Retries use exponential back-off on connection errors and HTTP 5xx/429.
* Downloads stream to disk, so large files never sit in memory.
* A failure raises :class:`SourceUnavailableError` — callers must handle it; nothing is
  ever silently substituted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from catrix.config import get_settings
from catrix.logging import get_logger

log = get_logger(__name__)


class SourceUnavailableError(RuntimeError):
    """Raised when a dataset cannot be retrieved. Carries the URL and HTTP detail."""

    def __init__(self, source_id: str, url: str, reason: str):
        super().__init__(f"[{source_id}] {reason} ({url})")
        self.source_id = source_id
        self.url = url
        self.reason = reason


@dataclass
class Download:
    path: Path
    url: str
    retrieved_at: datetime
    size_bytes: int
    sha256: str
    content_type: str | None
    from_cache: bool


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


def _client(timeout: float | None = None, headers: dict[str, str] | None = None) -> httpx.Client:
    s = get_settings()
    h = {"User-Agent": s.http_user_agent}
    if headers:
        h.update(headers)
    verify: bool | str = str(s.http_ca_bundle) if s.http_ca_bundle else s.http_verify_ssl
    return httpx.Client(
        timeout=timeout or s.http_timeout_seconds, headers=h, follow_redirects=True, verify=verify
    )


def cache_path_for(url: str, suffix: str = "") -> Path:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return get_settings().cache_dir / f"{key}{suffix}"


def download_file(
    source_id: str,
    url: str,
    *,
    dest: Path | None = None,
    force: bool = False,
    headers: dict[str, str] | None = None,
    expected_min_bytes: int = 1,
) -> Download:
    """Download ``url`` to ``dest`` (or the cache) with retries. Returns metadata."""
    settings = get_settings()
    settings.ensure_dirs()
    suffix = Path(httpx.URL(url).path).suffix
    target = dest or cache_path_for(url, suffix)
    meta_path = target.with_suffix(target.suffix + ".meta.json")

    if target.exists() and meta_path.exists() and not force:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        log.info("cache_hit", source=source_id, url=url, path=str(target))
        return Download(
            path=target,
            url=url,
            retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
            size_bytes=meta["size_bytes"],
            sha256=meta["sha256"],
            content_type=meta.get("content_type"),
            from_cache=True,
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(settings.http_max_retries),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable),
    )
    def _fetch() -> Download:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        size = 0
        with _client(headers=headers) as client, client.stream("GET", url) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type")
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        if size < expected_min_bytes:
            tmp.unlink(missing_ok=True)
            raise SourceUnavailableError(source_id, url, f"downloaded only {size} bytes")
        tmp.replace(target)
        now = datetime.now(UTC)
        meta = {
            "url": url,
            "source_id": source_id,
            "retrieved_at": now.isoformat(),
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "content_type": ctype,
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log.info("downloaded", source=source_id, url=url, bytes=size, path=str(target))
        return Download(target, url, now, size, meta["sha256"], ctype, False)

    try:
        return _fetch()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = ""
        if code in (401, 403):
            hint = " (authentication or access token required)"
        raise SourceUnavailableError(source_id, url, f"HTTP {code}{hint}") from exc
    except httpx.TransportError as exc:
        raise SourceUnavailableError(source_id, url, f"transport error: {exc!r}") from exc


def get_json(
    source_id: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Any:
    """GET a JSON document with retries (not cached — used for small API calls)."""
    settings = get_settings()

    @retry(
        reraise=True,
        stop=stop_after_attempt(settings.http_max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception(_is_retryable),
    )
    def _get() -> Any:
        with _client(timeout=timeout, headers=headers) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    try:
        return _get()
    except httpx.HTTPStatusError as exc:
        raise SourceUnavailableError(
            source_id, str(exc.request.url), f"HTTP {exc.response.status_code}"
        ) from exc
    except httpx.TransportError as exc:
        raise SourceUnavailableError(source_id, url, f"transport error: {exc!r}") from exc
    except json.JSONDecodeError as exc:
        raise SourceUnavailableError(source_id, url, "response was not JSON") from exc


def get_text(source_id: str, url: str, *, params: dict[str, Any] | None = None) -> str:
    settings = get_settings()

    @retry(
        reraise=True,
        stop=stop_after_attempt(settings.http_max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception(_is_retryable),
    )
    def _get() -> str:
        with _client() as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.text

    try:
        return _get()
    except httpx.HTTPStatusError as exc:
        raise SourceUnavailableError(
            source_id, str(exc.request.url), f"HTTP {exc.response.status_code}"
        ) from exc
    except httpx.TransportError as exc:
        raise SourceUnavailableError(source_id, url, f"transport error: {exc!r}") from exc
