"""Download the image the client sent (Instagram CDN, a link from the HTTP API)."""

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_S = 20.0


class FetchError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Fetched:
    data: bytes
    content_type: str
    sha256: str


def _private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            return True
    return False


def check_url(url: str, *, allow_private: bool = False) -> None:
    """Only http(s) to public addresses: a client must not make the server fetch its own
    internal services.
    TODO(review): DNS may change between this check and the request (rebinding); move
    the downloads to an egress proxy when the receipts go to production."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FetchError("url_invalid")
    if not allow_private and _private(parsed.hostname):
        raise FetchError("url_private")


async def fetch(
    http: httpx.AsyncClient, url: str, *, max_bytes: int = MAX_BYTES, allow_private: bool = False
) -> Fetched:
    check_url(url, allow_private=allow_private)
    try:
        async with http.stream("GET", url, timeout=TIMEOUT_S, follow_redirects=False) as response:
            if response.status_code != 200:
                raise FetchError(f"http_{response.status_code}")
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("too_large")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise FetchError("network") from exc
    data = b"".join(chunks)
    if not data:
        raise FetchError("empty")
    return Fetched(data, content_type.lower(), hashlib.sha256(data).hexdigest())
