"""Thin client for an OpenAI-compatible endpoint serving Gemma."""
import logging
import re
import threading
import time

import httpx

from .config import settings

log = logging.getLogger("hivemind.llm")


class LLMError(Exception):
    """Carries a message that is safe to show to the end user."""


_THOUGHT = re.compile(r"<(thought|think|thinking)>.*?(?:</\1>|\Z)", re.S | re.I)


def strip_thoughts(text: str) -> str:
    """Remove a thinking model's reasoning, which arrives inline in the reply."""
    return _THOUGHT.sub("", text).strip()


THINK_BRIEFLY = (
    "Reasoning budget: think for at most three short lines, then answer immediately. Do not "
    "restate these instructions, list constraints, draft alternatives or double-check wording."
)

_client = httpx.Client(timeout=httpx.Timeout(settings.llm_timeout, connect=10))
_status_lock = threading.Lock()
_status_cache: tuple[float, dict] | None = None


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}


def prepare(system: str, messages: list[dict]) -> list[dict]:
    """Shape a conversation the way Gemma wants it.

    Gemma has no system role and expects strictly alternating user/model
    turns, so the system prompt is folded into the first user turn and
    consecutive same-role turns are merged.
    """
    turns: list[dict] = []
    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        role = "assistant" if m.get("role") == "assistant" else "user"
        if not turns and role == "assistant":
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + content
        else:
            turns.append({"role": role, "content": content})
    if not turns:
        turns = [{"role": "user", "content": "Hello"}]
    if system.strip():
        turns[0]["content"] = f"{system.strip()}\n\n---\n\n{turns[0]['content']}"
    return turns


def chat(system: str, messages: list[dict], max_tokens: int = 700, temperature: float = 0.7,
         retry_empty: bool = True) -> str:
    """One Gemma completion. Raises LLMError with a user-safe message."""
    if settings.llm_needs_key:
        raise LLMError("This server has no GEMINI_API_KEY configured yet, so agents can't answer.")
    thinking = settings.llm_thinking_tokens
    if thinking:
        # Measured on gemma-4: leading with this roughly halves reasoning time.
        system = f"{THINK_BRIEFLY}\n\n{system}".strip()
    turns = prepare(system, messages)
    # A thinking model occasionally reasons in circles until the token limit
    # and never answers; one more attempt almost always succeeds.
    budgets = [thinking, thinking] if thinking and retry_empty else [thinking]
    for thinking in budgets:
        body = {
            "model": settings.llm_model,
            "messages": turns,
            "max_tokens": max_tokens + thinking,
            "temperature": temperature,
        }
        text = _complete(body)
        if text:
            return text
        log.warning("Gemma used its whole budget (%s tokens) without answering", body["max_tokens"])
    raise LLMError("The model didn't produce an answer. Please try again or rephrase.")


def _complete(body: dict) -> str:
    r = None
    for attempt in range(3):
        try:
            r = _client.post(f"{settings.llm_base_url}/chat/completions", json=body, headers=_headers())
        except httpx.TimeoutException:
            raise LLMError("Gemma took too long to answer. Please try again.")
        except httpx.HTTPError as e:
            log.warning("LLM unreachable: %s", e)
            raise LLMError("The Gemma model server isn't reachable right now. Please try again in a minute.")
        # Free-tier rate limits and brief overloads usually clear within seconds.
        if r.status_code in (429, 500, 503) and attempt < 2:
            time.sleep(2 + 4 * attempt)
            continue
        break
    if r.status_code in (401, 403):
        log.error("LLM rejected the API key: %s", r.text[:300])
        raise LLMError("The server's Gemma API key was rejected. Ask the admin to check GEMINI_API_KEY.")
    if r.status_code == 404:
        log.error("LLM model not found: %s", r.text[:300])
        raise LLMError(f"The model {settings.llm_model} isn't available on the model server yet.")
    if r.status_code == 429:
        raise LLMError("Gemma is rate-limited right now. Please try again in a minute.")
    if r.status_code >= 400:
        log.warning("LLM error %s: %s", r.status_code, r.text[:500])
        raise LLMError("The model returned an error. Please try again.")
    try:
        return strip_thoughts(r.json()["choices"][0]["message"]["content"] or "")
    except (ValueError, KeyError, IndexError, TypeError):
        log.warning("Unexpected LLM response: %s", r.text[:500])
        raise LLMError("The model returned an unreadable answer. Please try again.")


def status() -> dict:
    """Is the model server up, and is our model loaded on it? Cached briefly."""
    global _status_cache
    with _status_lock:
        if _status_cache and time.time() - _status_cache[0] < 10:
            return _status_cache[1]
    result = {
        "model": settings.llm_model,
        "reachable": False,
        "model_ready": False,
        "key_missing": settings.llm_needs_key,
    }
    try:
        if settings.llm_needs_key:
            raise httpx.HTTPError("no api key")
        r = _client.get(f"{settings.llm_base_url}/models", headers=_headers(), timeout=5)
        if r.status_code < 400:
            result["reachable"] = True
            want = settings.llm_model
            ids = [str(m.get("id", "")) for m in r.json().get("data", [])]
            result["model_ready"] = any(
                i == want or i == f"{want}:latest" or i.endswith("/" + want) for i in ids
            )
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    with _status_lock:
        _status_cache = (time.time(), result)
    return result
