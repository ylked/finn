"""Thin wrapper around any OpenAI-compatible chat completions endpoint."""

from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from ..config import get_settings

log = logging.getLogger(__name__)
_client: OpenAI | None = None


class AIError(RuntimeError):
    pass


def client() -> OpenAI:
    global _client
    s = get_settings()
    if not s.ai_base_url:
        raise AIError("AI is not configured")
    if _client is None:
        _client = OpenAI(base_url=s.ai_base_url, api_key=s.ai_api_key or "none", timeout=s.ai_timeout, max_retries=2)
    return _client


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError(f"Model did not return valid JSON: {text[:200]}") from e
    if not isinstance(data, dict):
        raise AIError("Model JSON is not an object")
    return data


def chat_json(model: str, messages: list[dict], temperature: float = 0.0) -> dict:
    """Ask for a JSON object. Falls back to plain completion if response_format is unsupported."""
    c = client()
    try:
        resp = c.chat.completions.create(
            model=model, messages=messages, temperature=temperature, response_format={"type": "json_object"}
        )
    except Exception as e:  # some compatible servers reject response_format
        msg = str(e).lower()
        if "response_format" not in msg and "json" not in msg:
            raise AIError(str(e)) from e
        log.info("response_format unsupported, retrying without it")
        try:
            resp = c.chat.completions.create(model=model, messages=messages, temperature=temperature)
        except Exception as e2:
            raise AIError(str(e2)) from e2
    content = resp.choices[0].message.content or ""
    return _extract_json(content)
