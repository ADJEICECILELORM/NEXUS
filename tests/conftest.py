"""Shared test fixtures: fake HTTP layer + real (recorded) API payload shapes."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from nexus.polymarket.http import HttpJsonClient


class FakeResponse(io.BytesIO):
    code = 200
    status = 200
    headers = {"Content-Encoding": ""}


def fake_urlopen_factory(routes):
    """routes: {url_substring: payload_dict_or_list} → urlopen replacement.

    A route value may also be a callable(request_url) -> payload, or an
    exception instance to raise (HTTPError with code attr).
    """

    def urlopen(request, timeout=None):
        url = request.full_url
        for key, payload in routes.items():
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                if callable(payload):
                    payload = payload(url)
                return FakeResponse(json.dumps(payload).encode())
        raise urllib.error.HTTPError(
            url, 404, "no such route", {"Content-Type": "text/plain"}, io.BytesIO(b"nf")
        )

    return urlopen


@pytest.fixture
def http_client():
    return HttpJsonClient("https://base.example")
