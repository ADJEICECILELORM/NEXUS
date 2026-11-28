"""LLM client layer for the ORACLE scorecard.

One fixed model per D6 phase. The client returns P(YES) as a float in
[0, 1] or raises LlmError. A deterministic FakeLlm backs the tests.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable, Protocol

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"

_PROB_RE = re.compile(
    r"PROBABILITY\s*[:=]\s*([0-9]*\.?[0-9]+)\s*(%?)", re.IGNORECASE
)


class LlmError(RuntimeError):
    pass


def parse_probability(text: str) -> float:
    """Extract P(YES) from an LLM reply. Raises LlmError if absent/out of range."""
    match = _PROB_RE.search(text or "")
    if match is None:
        raise LlmError(f"no 'PROBABILITY:' line in reply: {text[:200]!r}")
    value = float(match.group(1))
    if match.group(2):          # explicit percent sign
        value = value / 100.0
    if not 0.0 <= value <= 1.0:
        raise LlmError(f"probability out of range: {value}")
    return value


class LlmClient(Protocol):
    def forecast(self, question: str, rules: str) -> float:
        ...


class AnthropicClient:
    """Minimal Anthropic Messages API client (stdlib urllib, no deps)."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 200,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise LlmError("AnthropicClient requires an API key")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries

    def _post(self, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                request = urllib.request.Request(
                    ANTHROPIC_URL,
                    data=data,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": self.api_key,
                        "anthropic-version": ANTHROPIC_VERSION,
                    },
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                if exc.code in (429, 500, 502, 503, 504, 529) and attempt < self.max_retries - 1:
                    last_exc = LlmError(f"HTTP {exc.code}: {detail}")
                    continue
                raise LlmError(f"HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    continue
                raise LlmError(f"network error calling Anthropic: {exc}") from exc
        raise LlmError(f"exhausted retries: {last_exc}")

    def forecast(self, question: str, rules: str) -> float:
        prompt = build_prompt(question, rules)
        payload = self._post(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        try:
            text = "".join(
                block.get("text", "")
                for block in payload.get("content", [])
                if isinstance(block, dict)
            )
        except Exception as exc:  # malformed payload
            raise LlmError(f"malformed Anthropic response: {exc}") from exc
        return parse_probability(text)


class FakeLlm:
    """Deterministic test double: maps question substring -> probability."""

    def __init__(self, probs: dict[str, float] | float | Callable[[str], float]) -> None:
        self.probs = probs
        self.calls: list[str] = []

    def forecast(self, question: str, rules: str) -> float:
        self.calls.append(question)
        if callable(self.probs):
            return float(self.probs(question))
        if isinstance(self.probs, (int, float)):
            return float(self.probs)
        for key, prob in self.probs.items():
            if key.lower() in question.lower():
                return float(prob)
        raise LlmError(f"FakeLlm has no canned answer for {question!r}")


def build_prompt(question: str, rules: str) -> str:
    rules_line = rules.strip() or (
        "as written in the question; resolve to the most natural reading"
    )
    return (
        "You are forecasting the outcome of a prediction-market question.\n\n"
        f"Question: {question}\n\n"
        f"Resolution rules: {rules_line}\n\n"
        "Give your best honest probability that the answer is YES, using only "
        "your own knowledge and reasoning. Do not try to guess what other "
        "traders believe.\n\n"
        "End your reply with exactly one line of the form:\n"
        "PROBABILITY: 0.XX   (a decimal in [0, 1], e.g. 0.42 or 0.85)"
    )
