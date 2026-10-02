"""Instagram API with Instagram Login: sending messages, the login flow, tokens.

Graph API: `IG_GRAPH_URL` (https://graph.instagram.com/v23.0). Token exchange and
refresh live on the unversioned host. Tokens never reach logs: errors carry only the
Graph error code / type, never the request URL (GET calls have the token in the query).
"""

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from app.logging import get_logger
from app.settings import Settings

log = get_logger(__name__)

SCOPES = ("instagram_business_basic", "instagram_business_manage_messages")
# Instagram cuts a text message at 1000 characters
MAX_TEXT = 1000

# Graph error codes (developers.facebook.com/docs/instagram-platform … error codes)
AUTH_CODES = {190, 102, 463, 467}
RATE_CODES = {4, 17, 32, 613, 80002, 80006}
TEMPORARY_CODES = {1, 2, -1}
WINDOW_SUBCODES = {2018278}  # "This message is sent outside of allowed window"


class InstagramError(Exception):
    def __init__(
        self,
        status: int,
        message: str = "",
        *,
        code: int | None = None,
        subcode: int | None = None,
        kind_hint: str | None = None,
    ) -> None:
        super().__init__(message or f"instagram http {status}")
        self.status = status
        self.code = code
        self.subcode = subcode
        self._kind_hint = kind_hint

    @property
    def kind(self) -> str:
        """auth | window | temporary | permanent."""
        if self._kind_hint:
            return self._kind_hint
        if self.code in AUTH_CODES or self.status == 401:
            return "auth"
        if self.subcode in WINDOW_SUBCODES or "allowed window" in str(self).lower():
            return "window"
        if self.status >= 500 or self.status in (408, 429) or self.code in RATE_CODES:
            return "temporary"
        if self.code in TEMPORARY_CODES:
            return "temporary"
        return "permanent"

    def short(self) -> str:
        """Safe for logs and the admin: no tokens, no URLs."""
        parts = [f"http {self.status}"]
        if self.code is not None:
            parts.append(f"code {self.code}")
        if self.subcode is not None:
            parts.append(f"subcode {self.subcode}")
        text = str(self)[:300]
        return f"{text} ({', '.join(parts)})"


@dataclass(frozen=True)
class TokenInfo:
    token: str = field(repr=False)
    expires_in: int | None = None  # seconds
    user_id: str | None = None
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Profile:
    user_id: str  # the IG professional account id: `recipient.id` in webhooks
    username: str | None
    name: str | None
    picture: str | None


def _unversioned(url: str) -> str:
    return re.sub(r"/v\d+(\.\d+)?/?$", "", url.rstrip("/"))


def _error_from(resp: httpx.Response) -> InstagramError:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return InstagramError(
            resp.status_code,
            str(err.get("message") or ""),
            code=err.get("code") if isinstance(err.get("code"), int) else None,
            subcode=err.get("error_subcode") if isinstance(err.get("error_subcode"), int) else None,
        )
    if isinstance(data, dict) and data.get("error_message"):
        # api.instagram.com/oauth/access_token style
        return InstagramError(
            resp.status_code,
            str(data.get("error_message")),
            code=data.get("code") if isinstance(data.get("code"), int) else None,
            kind_hint="auth" if data.get("error_type") == "OAuthException" else None,
        )
    return InstagramError(resp.status_code, f"instagram http {resp.status_code}")


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Cut a long reply into messages of at most `limit` characters: at a paragraph,
    a line, a sentence or a space in the second half of the piece; otherwise hard."""
    rest = text.strip()
    parts: list[str] = []
    while len(rest) > limit:
        window = rest[:limit]
        cut = limit
        for sep in ("\n\n", "\n", ". ", "! ", "? ", "… ", " "):
            i = window.rfind(sep)
            if i >= limit // 2:
                cut = i + len(sep)
                break
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


class InstagramClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings
        self.graph = settings.ig_graph_url.rstrip("/")
        self.graph_root = _unversioned(settings.ig_graph_url)
        self.timeout = settings.ig_timeout_s

    @property
    def configured(self) -> bool:
        s = self.settings
        return bool(s.meta_app_id and s.meta_app_secret.get_secret_value())

    async def _call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = await self.http.request(method, url, timeout=self.timeout, **kwargs)
        except httpx.HTTPError as exc:
            # str(exc) may hold the URL with the token: only the type goes on
            raise InstagramError(0, type(exc).__name__, kind_hint="temporary") from None
        if resp.status_code >= 400:
            raise _error_from(resp)
        try:
            data = resp.json()
        except ValueError:
            raise InstagramError(resp.status_code, "not json", kind_hint="temporary") from None
        return data if isinstance(data, dict) else {"data": data}

    # --- messages ---------------------------------------------------------------------

    async def send_text(
        self,
        account_id: str,
        token: str,
        recipient_id: str,
        text: str,
        *,
        human_agent: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"recipient": {"id": recipient_id}, "message": {"text": text}}
        if human_agent:
            body["messaging_type"] = "MESSAGE_TAG"
            body["tag"] = "HUMAN_AGENT"
        return await self._call(
            "POST",
            f"{self.graph}/{account_id}/messages",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    # --- login and tokens -------------------------------------------------------------

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.settings.meta_app_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": ",".join(SCOPES),
                "state": state,
                # TODO(review): force_reauth=true makes Instagram ask which account to
                # connect even when one is signed in — handy for several accounts
                "force_reauth": "true",
            }
        )
        return f"{self.settings.ig_oauth_url}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenInfo:
        """Authorization code → short-lived token (1 hour)."""
        data = await self._call(
            "POST",
            self.settings.ig_token_url,
            data={
                "client_id": self.settings.meta_app_id,
                "client_secret": self.settings.meta_app_secret.get_secret_value(),
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        # newer responses wrap it: {"data": [{"access_token", "user_id", "permissions"}]}
        item = data["data"][0] if isinstance(data.get("data"), list) and data["data"] else data
        token = item.get("access_token")
        if not token:
            raise InstagramError(200, "no access_token in the answer", kind_hint="permanent")
        perms = item.get("permissions") or ()
        if isinstance(perms, str):
            perms = tuple(p for p in perms.split(",") if p)
        return TokenInfo(
            token=token,
            user_id=str(item["user_id"]) if item.get("user_id") is not None else None,
            permissions=tuple(perms),
        )

    async def long_lived(self, short_token: str) -> TokenInfo:
        """Short-lived → long-lived token (60 days)."""
        data = await self._call(
            "GET",
            f"{self.graph_root}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": self.settings.meta_app_secret.get_secret_value(),
                "access_token": short_token,
            },
        )
        return TokenInfo(token=data["access_token"], expires_in=data.get("expires_in"))

    async def refresh(self, token: str) -> TokenInfo:
        """A long-lived token at least 24 hours old → a new one for 60 days."""
        data = await self._call(
            "GET",
            f"{self.graph_root}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
        )
        return TokenInfo(token=data["access_token"], expires_in=data.get("expires_in"))

    async def me(self, token: str) -> Profile:
        data = await self._call(
            "GET",
            f"{self.graph}/me",
            params={
                "fields": "user_id,username,name,profile_picture_url",
                "access_token": token,
            },
        )
        user_id = data.get("user_id") or data.get("id")
        if not user_id:
            raise InstagramError(200, "no user_id in /me", kind_hint="permanent")
        return Profile(
            user_id=str(user_id),
            username=data.get("username"),
            name=data.get("name"),
            picture=data.get("profile_picture_url"),
        )

    async def user_profile(self, token: str, igsid: str) -> dict[str, Any]:
        """Name and username of a client who wrote to us (IGSID from the webhook)."""
        return await self._call(
            "GET",
            f"{self.graph}/{igsid}",
            params={"fields": "name,username", "access_token": token},
        )

    async def subscribe(self, token: str) -> None:
        """Subscribe the account to the `messages` webhook of our app."""
        data = await self._call(
            "POST",
            f"{self.graph}/me/subscribed_apps",
            params={"subscribed_fields": "messages", "access_token": token},
        )
        if data.get("success") is False:
            raise InstagramError(200, "subscribe failed", kind_hint="permanent")
