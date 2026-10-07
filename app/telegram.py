"""Minimal Telegram Bot API client and Markdown-to-Telegram formatting."""
import html
import logging
import re
import time

import httpx

log = logging.getLogger("hivemind.telegram")


class TelegramError(Exception):
    def __init__(self, code: int, description: str, retry_after: int = 0):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description
        self.retry_after = retry_after


def split_text(text: str, limit: int = 3500) -> list[str]:
    """Split on paragraph/line boundaries so each part fits in one message."""
    parts = []
    text = text.strip()
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def _inline(text: str) -> str:
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", stash, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*$", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^(\s*)[*\-]\s+", r"\1• ", text)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\*\w])\*(?=[^\s\*])([^\*\n]+?)(?<=[^\s\*])\*(?![\*\w])", r"<i>\1</i>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))], quote=False)}</code>", text)


def to_html(text: str) -> str:
    """Convert the Markdown Gemma tends to write into Telegram's HTML subset."""
    text = text.replace("\x00", "")
    out = []
    for i, segment in enumerate(text.split("```")):
        if i % 2:
            body = re.sub(r"^[\w+\-#.]*\n", "", segment, count=1)  # drop the language tag
            out.append(f"<pre>{html.escape(body.strip(chr(10)), quote=False)}</pre>")
        else:
            out.append(_inline(segment))
    return "".join(out)


class Telegram:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org"):
        self.token = token
        self.api_base = api_base
        self.http = httpx.Client(timeout=30)

    def call(self, method: str, _timeout: float = 30, **params):
        r = self.http.post(f"{self.api_base}/bot{self.token}/{method}", json=params, timeout=_timeout)
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(r.status_code, "non-JSON response from Telegram")
        if not data.get("ok"):
            raise TelegramError(
                int(data.get("error_code") or r.status_code),
                str(data.get("description") or "unknown error"),
                int((data.get("parameters") or {}).get("retry_after") or 0),
            )
        return data.get("result")

    def download(self, file_id: str, max_bytes: int) -> bytes:
        info = self.call("getFile", file_id=file_id)
        r = self.http.get(f"{self.api_base}/file/bot{self.token}/{info['file_path']}", timeout=60)
        r.raise_for_status()
        if len(r.content) > max_bytes:
            raise ValueError("file too large")
        return r.content

    def _send_one(self, chat_id: int, text: str, reply_markup: dict | None) -> None:
        extra = {"reply_markup": reply_markup} if reply_markup else {}
        for attempt in range(2):
            try:
                try:
                    self.call(
                        "sendMessage", chat_id=chat_id, text=to_html(text), parse_mode="HTML",
                        link_preview_options={"is_disabled": True}, **extra,
                    )
                except TelegramError as e:
                    if e.code != 400:
                        raise
                    # Formatting Telegram couldn't parse: fall back to plain text.
                    self.call("sendMessage", chat_id=chat_id, text=text, **extra)
                return
            except TelegramError as e:
                if e.code == 429 and attempt == 0:
                    time.sleep(min(e.retry_after or 1, 30))
                    continue
                if e.code == 403:
                    log.info("Chat %s has blocked the bot", chat_id)
                    return
                raise

    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        parts = split_text(text) or ["…"]
        for i, part in enumerate(parts):
            self._send_one(chat_id, part, reply_markup if i == len(parts) - 1 else None)
