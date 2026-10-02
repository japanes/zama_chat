"""Admin sign-in: email link (MemoryMailer), Google (mocked HTTP), sessions."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.admin.auth import SESSION_COOKIE, STATE_COOKIE
from app.admin.mailer import MemoryMailer, mail_texts
from app.db.models import AdminSession, LoginToken, User
from tests.admin_helpers import admin_headers, make_user
from tests.factories import make_tenant

pytestmark = pytest.mark.db


@pytest.fixture
def mailer(services) -> MemoryMailer:
    m = MemoryMailer()
    services.extra["mailer"] = m
    return m


def _token_from(mail) -> str:
    link = next(w for w in mail.text.split() if "/auth/magic?token=" in w)
    return parse_qs(urlparse(link).query)["token"][0]


async def _verify(client, token: str) -> httpx.Response:
    return await client.post("/v1/admin/auth/magic-link/verify", json={"token": token})


# --- email link ---------------------------------------------------------------------------


async def test_magic_link_signs_in_a_known_user(client, services, mailer):
    await make_user(services, "olha@example.com")
    resp = await client.post("/v1/admin/auth/magic-link", json={"email": " Olha@Example.com "})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}  # no dev echo outside dev settings
    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == "olha@example.com"

    resp = await _verify(client, _token_from(mailer.sent[0]))
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "olha@example.com"
    cookie = resp.cookies.get(SESSION_COOKIE)
    assert cookie

    me = await client.get("/v1/admin/auth/me", headers={"Cookie": f"{SESSION_COOKIE}={cookie}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "olha@example.com"


async def test_unknown_address_gets_the_same_answer_and_no_mail(client, services, mailer):
    resp = await client.post("/v1/admin/auth/magic-link", json={"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert mailer.sent == []


async def test_inactive_user_gets_no_mail(client, services, mailer):
    await make_user(services, "gone@example.com", active=False)
    resp = await client.post("/v1/admin/auth/magic-link", json={"email": "gone@example.com"})
    assert resp.json() == {"ok": True}
    assert mailer.sent == []


async def test_one_link_per_minute_per_address(client, services, mailer, clock):
    await make_user(services, "olha@example.com")
    for _ in range(3):
        await client.post("/v1/admin/auth/magic-link", json={"email": "olha@example.com"})
    assert len(mailer.sent) == 1
    clock.advance(seconds=61)
    await client.post("/v1/admin/auth/magic-link", json={"email": "olha@example.com"})
    assert len(mailer.sent) == 2


async def test_link_is_single_use(client, services, mailer):
    await make_user(services, "olha@example.com")
    await client.post("/v1/admin/auth/magic-link", json={"email": "olha@example.com"})
    token = _token_from(mailer.sent[0])
    assert (await _verify(client, token)).status_code == 200
    again = await _verify(client, token)
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "auth.bad_token"


async def test_expired_link(client, services, mailer, clock):
    await make_user(services, "olha@example.com")
    await client.post("/v1/admin/auth/magic-link", json={"email": "olha@example.com"})
    clock.advance(minutes=services.settings.magic_link_minutes + 1)
    resp = await _verify(client, _token_from(mailer.sent[0]))
    assert resp.json()["error"]["code"] == "auth.link_expired"


async def test_wrong_token(client, services, mailer):
    resp = await _verify(client, "not-a-token")
    assert resp.json()["error"]["code"] == "auth.bad_token"


async def test_only_the_hash_of_the_token_is_stored(client, services, mailer):
    await make_user(services, "olha@example.com")
    await client.post("/v1/admin/auth/magic-link", json={"email": "olha@example.com"})
    token = _token_from(mailer.sent[0])
    async with services.sm() as session:
        rows = list((await session.execute(select(LoginToken))).scalars())
    assert len(rows) == 1
    assert rows[0].token_hash != token
    assert token not in rows[0].token_hash


async def test_mail_failure_does_not_leave_a_valid_link(client, services, mailer):
    await make_user(services, "olha@example.com")

    class Broken:
        async def send(self, mail):
            raise OSError("smtp down")

    services.extra["mailer"] = Broken()
    resp = await client.post("/v1/admin/auth/magic-link", json={"email": "olha@example.com"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "auth.mail_failed"
    async with services.sm() as session:
        assert list((await session.execute(select(LoginToken))).scalars()) == []


async def test_no_mailer_and_no_dev_echo(client, services):
    services.extra.pop("mailer", None)
    resp = await client.post("/v1/admin/auth/magic-link", json={"email": "olha@example.com"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "auth.mail_not_configured"


async def test_user_locale_picks_the_mail_language(client, services, mailer):
    await make_user(services, "olha@example.com")
    async with services.sm() as session, session.begin():
        user = (
            await session.execute(select(User).where(User.email == "olha@example.com"))
        ).scalar_one()
        user.locale = "uk"
    # the browser asks for en, the user chose uk in the admin: the user's choice wins
    await client.post(
        "/v1/admin/auth/magic-link", json={"email": "olha@example.com", "locale": "en"}
    )
    assert mailer.sent[0].subject == mail_texts("uk")["magic_link"]["subject"]
    assert mail_texts("uk")["magic_link"]["subject"] != mail_texts("en")["magic_link"]["subject"]


# --- sessions -----------------------------------------------------------------------------


async def test_no_cookie_is_401(client):
    resp = await client.get("/v1/admin/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.required"


async def test_logout_ends_the_session(client, services):
    headers = await admin_headers(services)
    assert (await client.get("/v1/admin/auth/me", headers=headers)).status_code == 200
    assert (await client.post("/v1/admin/auth/logout", headers=headers)).status_code == 200
    assert (await client.get("/v1/admin/auth/me", headers=headers)).status_code == 401


async def test_expired_session(client, services, clock):
    headers = await admin_headers(services)
    clock.advance(days=services.settings.session_days + 1)
    assert (await client.get("/v1/admin/auth/me", headers=headers)).status_code == 401


async def test_deactivated_user_loses_access(client, services):
    super_headers = await admin_headers(services, "root@example.com", superadmin=True)
    headers = await admin_headers(services, "olha@example.com")
    async with services.sm() as session:
        user_id = (
            await session.execute(select(User.id).where(User.email == "olha@example.com"))
        ).scalar_one()
    resp = await client.patch(
        f"/v1/admin/users/{user_id}", json={"is_active": False}, headers=super_headers
    )
    assert resp.status_code == 200
    assert (await client.get("/v1/admin/auth/me", headers=headers)).status_code == 401
    async with services.sm() as session:
        left = (
            await session.execute(select(AdminSession).where(AdminSession.user_id == user_id))
        ).all()
    assert left == []


async def test_me_lists_only_own_tenants(client, services):
    a = await make_tenant(services.sm, slug="a", title="A")
    await make_tenant(services.sm, slug="b", title="B")
    headers = await admin_headers(services, tenant_id=a.tenant_id, role="operator")
    tenants = (await client.get("/v1/admin/auth/me", headers=headers)).json()["tenants"]
    assert [(t["slug"], t["role"]) for t in tenants] == [("a", "operator")]


async def test_patch_me_locale(client, services):
    headers = await admin_headers(services)
    resp = await client.patch("/v1/admin/auth/me", json={"locale": "uk"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user"]["locale"] == "uk"
    bad = await client.patch("/v1/admin/auth/me", json={"locale": "Ukrainian!"}, headers=headers)
    assert bad.json()["error"]["code"] == "user.locale_invalid"


# --- Google -------------------------------------------------------------------------------


class GoogleStub:
    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.token_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            self.token_requests.append(request)
            return httpx.Response(200, json={"access_token": "g-access"})
        if "userinfo" in request.url.path:
            assert request.headers["authorization"] == "Bearer g-access"
            return httpx.Response(200, json=self.profile)
        return httpx.Response(404)


@pytest.fixture
def google(services):
    services.settings = services.settings.model_copy(
        update={"google_client_id": "cid", "google_client_secret": SecretStr("csecret")}
    )

    def use(profile: dict) -> GoogleStub:
        stub = GoogleStub(profile)
        services.http = httpx.AsyncClient(transport=httpx.MockTransport(stub))
        return stub

    return use


async def _google_flow(client, *, next_path: str = "/results") -> httpx.Response:
    """start → (Google) → callback; the state cookie travels in the client's jar."""
    start = await client.get("/v1/admin/auth/google/start", params={"next": next_path})
    assert start.status_code == 302
    query = parse_qs(urlparse(start.headers["location"]).query)
    assert query["client_id"] == ["cid"]
    assert STATE_COOKIE in start.cookies
    return await client.get(
        "/v1/admin/auth/google/callback", params={"code": "c1", "state": query["state"][0]}
    )


async def test_google_binds_sub_on_first_sign_in(client, services, google):
    await make_user(services, "olha@example.com")
    google({"sub": "g-1", "email": "Olha@example.com", "email_verified": True, "name": "Olha"})
    resp = await _google_flow(client)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/results")
    assert resp.cookies.get(SESSION_COOKIE)
    async with services.sm() as session:
        user = (
            await session.execute(select(User).where(User.email == "olha@example.com"))
        ).scalar_one()
    assert user.google_sub == "g-1"
    assert user.name == "Olha"


async def test_google_unknown_email_has_no_access(client, services, google):
    google({"sub": "g-2", "email": "stranger@example.com", "email_verified": True})
    resp = await _google_flow(client)
    assert "/login?error=auth.no_access" in resp.headers["location"]
    assert not resp.cookies.get(SESSION_COOKIE)


async def test_google_unverified_email_is_refused(client, services, google):
    await make_user(services, "olha@example.com")
    google({"sub": "g-3", "email": "olha@example.com", "email_verified": False})
    resp = await _google_flow(client)
    assert "auth.google_email_unverified" in resp.headers["location"]


async def test_google_state_mismatch(client, services, google):
    google({"sub": "g-1", "email": "olha@example.com", "email_verified": True})
    resp = await client.get(
        "/v1/admin/auth/google/callback?code=c1&state=forged",
        headers={"Cookie": f"{STATE_COOKIE}=real"},
    )
    assert "auth.google_state" in resp.headers["location"]


async def test_google_next_never_leaves_the_admin(client, services, google):
    await make_user(services, "olha@example.com")
    google({"sub": "g-1", "email": "olha@example.com", "email_verified": True})
    resp = await _google_flow(client, next_path="//evil.example.com/x")
    location = resp.headers["location"]
    assert location == services.settings.admin_url.rstrip("/") + "/"


async def test_google_not_configured(client, services):
    resp = await client.get("/v1/admin/auth/google/start")
    assert resp.status_code == 302
    assert "auth.google_not_configured" in resp.headers["location"]
