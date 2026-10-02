"""Telegram Bot API: sendMessage. The token is in the URL, so errors carry only the
Telegram description, never the URL."""

import httpx

MAX_TEXT = 4000  # Telegram's limit is 4096


class TelegramError(Exception):
    def __init__(self, status: int, description: str) -> None:
        super().__init__(f"telegram {status}: {description}")
        self.status = status
        self.description = description

    @property
    def permanent(self) -> bool:
        # a wrong token, a chat the bot is not in, a blocked bot: retrying will not help
        return 400 <= self.status < 500 and self.status != 429


async def send_message(
    http: httpx.AsyncClient, api_url: str, token: str, chat_id: str | int, text: str
) -> None:
    try:
        resp = await http.post(
            f"{api_url.rstrip('/')}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[:MAX_TEXT],
                "disable_web_page_preview": True,
            },
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise TelegramError(0, type(exc).__name__) from None
    if resp.status_code >= 400:
        try:
            description = str(resp.json().get("description") or "")
        except ValueError:
            description = ""
        raise TelegramError(resp.status_code, description[:300] or "error")
