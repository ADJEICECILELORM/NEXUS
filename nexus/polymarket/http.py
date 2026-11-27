"""Shared stdlib-only HTTP client for Polymarket public APIs.

Verified behaviors (docs/FINDINGS.md F0):
- Gamma error bodies and Data API error bodies are gzip-compressed
- Gamma /events with limit > 50 requires Accept-Encoding: gzip
- 429/5xx/network errors are retried with linear backoff; other 4xx fail hard
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_RETRIES = 3
RETRYABLE_CODES = {429, 500, 502, 503, 504}


class PublicApiError(Exception):
    """A public API returned an error response or unusable payload."""


def parse_jsonish(value: Any) -> Any:
    """Gamma stores JSON-encoded strings inside JSON fields ('[\"Yes\",\"No\"]').

    Decode those; pass through everything else; return the original string
    when it does not parse (tolerant by design).
    """
    if isinstance(value, str):
        s = value.strip()
        if s.startswith(("[", "{")) or (s.replace(".", "", 1).isdigit() and s):
            try:
                return json.loads(s)
            except (ValueError, TypeError):
                return value
    return value


def parse_str_list(value: Any) -> list[str]:
    parsed = parse_jsonish(value)
    if isinstance(parsed, list):
        return [str(x) for x in parsed]
    if isinstance(parsed, str):
        return [parsed]
    return []


def parse_float_list(value: Any) -> list[float | None]:
    parsed = parse_jsonish(value)
    if isinstance(parsed, list):
        out: list[float | None] = []
        for x in parsed:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                out.append(None)
        return out
    if isinstance(parsed, (int, float)):
        return [float(parsed)]
    return []


class HttpJsonClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = 0.5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    # ------------------------------------------------------------ transport
    def _request(self, path: str, params: dict[str, object] | None) -> tuple[bytes, int]:
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url += "?" + urllib.parse.urlencode(clean)
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read(), resp.status
            except urllib.error.HTTPError as e:
                body = e.read()
                if e.code in RETRYABLE_CODES and attempt < self.max_retries:
                    last_err = e
                    time.sleep(self.backoff_base * (attempt + 1))
                    continue
                raise PublicApiError(
                    f"GET {url} failed: HTTP {e.code}: {_decode_body(body)[:300]}"
                ) from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if attempt < self.max_retries:
                    last_err = e
                    time.sleep(self.backoff_base * (attempt + 1))
                    continue
                raise PublicApiError(f"GET {url} failed: {e!r}") from e
        raise PublicApiError(f"GET {url} failed after retries: {last_err!r}")

    # ------------------------------------------------------------ interface
    def get_text(self, path: str, params: dict[str, object] | None = None) -> tuple[str, int]:
        """Raw body as text, gzip-aware (response header OR magic bytes)."""
        body, status = self._request(path, params)
        return _decode_body(body), status

    def get(self, path: str, params: dict[str, object] | None = None) -> Any:
        text, _status = self.get_text(path, params)
        try:
            return json.loads(text)
        except (ValueError, TypeError) as e:
            raise PublicApiError(
                f"GET {self.base_url + path} returned non-JSON payload: {text[:200]!r}"
            ) from e


def _decode_body(body: bytes) -> str:
    """Decode a (possibly gzip/zlib-compressed) response body."""
    if not body:
        return ""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    elif body[:1] in (b"\x78", b"\x08"):
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
    return body.decode("utf-8", errors="replace")
