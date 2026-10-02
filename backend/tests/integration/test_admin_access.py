"""Who may do what in the admin: operator < admin < owner < superadmin, and nobody sees
another tenant (404, not 403, so ids of other tenants do not leak)."""

import pytest
from sqlalchemy import select

from app.db.models import ApiKey, AuditLog
from tests.admin_helpers import admin_headers
from tests.factories import make_tenant
from tests.scenario_helpers import interview_data

pytestmark = pytest.mark.db

LANGS = {"languages": ["uk", "en"]}


@pytest.fixture
async def tenant(services):
    return await make_tenant(services.sm, settings=LANGS)


async def test_operator_reads_but_does_not_change(client, services, tenant):
    tid = tenant.tenant_id
    h = await admin_headers(services, "op@example.com", tenant_id=tid, role="operator")

    for path in (
        f"/v1/admin/tenants/{tid}",
        f"/v1/admin/tenants/{tid}/results",
        f"/v1/admin/tenants/{tid}/conversations",
        f"/v1/admin/tenants/{tid}/scenarios",
    ):
        assert (await client.get(path, headers=h)).status_code == 200, path

    denied = [
        ("post", f"/v1/admin/tenants/{tid}/scenarios", {"definition": interview_data()}),
        ("post", "/v1/admin/prompts/system_prompt/versions", {"tenant_id": str(tid), "body": "x"}),
        ("put", "/v1/admin/roles/reply", {"tenant_id": str(tid), "model": "openai/x"}),
        ("patch", f"/v1/admin/tenants/{tid}", {"title": "New"}),
        ("post", f"/v1/admin/tenants/{tid}/api-keys", {"title": "k"}),
        ("post", f"/v1/admin/tenants/{tid}/sandbox/turn", {"text": "hi"}),
    ]
    for method, path, body in denied:
        resp = await client.request(method, path, json=body, headers=h)
        assert resp.status_code == 403, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "auth.forbidden"
        assert resp.json()["error"]["params"]["need"] == "admin"

    assert (await client.get("/v1/admin/models", headers=h)).status_code == 403
    assert (await client.get(f"/v1/admin/tenants/{tid}/members", headers=h)).status_code == 403


async def test_admin_cannot_manage_members_or_models(client, services, tenant):
    tid = tenant.tenant_id
    h = await admin_headers(services, "adm@example.com", tenant_id=tid, role="admin")
    assert (await client.get(f"/v1/admin/tenants/{tid}/members", headers=h)).status_code == 200
    resp = await client.post(
        f"/v1/admin/tenants/{tid}/members",
        json={"email": "new@example.com", "role": "operator"},
        headers=h,
    )
    assert resp.status_code == 403
    # models and shared roles are the superadmin's
    resp = await client.post("/v1/admin/models", json={"name": "openai/x"}, headers=h)
    assert resp.status_code == 403
    resp = await client.put("/v1/admin/roles/reply", json={"model": "openai/x"}, headers=h)
    assert resp.status_code == 403
    assert (await client.get("/v1/admin/models", headers=h)).status_code == 200


async def test_other_tenant_is_not_found(client, services, tenant):
    other = await make_tenant(services.sm, slug="other", title="Other")
    h = await admin_headers(services, "adm@example.com", tenant_id=tenant.tenant_id)
    resp = await client.get(f"/v1/admin/tenants/{other.tenant_id}", headers=h)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "tenant.not_found"


async def test_row_of_another_tenant_is_not_found(client, services, tenant):
    other = await make_tenant(services.sm, slug="other", title="Other")
    h = await admin_headers(services, "adm@example.com", tenant_id=tenant.tenant_id)
    # the other tenant's API key, addressed through our tenant
    resp = await client.delete(
        f"/v1/admin/tenants/{tenant.tenant_id}/api-keys/{other.api_key_id}", headers=h
    )
    assert resp.status_code == 404
    async with services.sm() as session:
        key = await session.get(ApiKey, other.api_key_id)
    assert key.revoked_at is None


async def test_superadmin_is_owner_everywhere(client, services, tenant):
    h = await admin_headers(services, "root@example.com", superadmin=True)
    tid = tenant.tenant_id
    assert (await client.get(f"/v1/admin/tenants/{tid}/members", headers=h)).status_code == 200
    resp = await client.post(
        f"/v1/admin/tenants/{tid}/members",
        json={"email": "new@example.com", "role": "operator"},
        headers=h,
    )
    assert resp.status_code == 200


async def test_only_superadmin_creates_tenants(client, services, tenant):
    h = await admin_headers(services, "own@example.com", tenant_id=tenant.tenant_id, role="owner")
    body = {"slug": "newco", "title": "New Co"}
    assert (await client.post("/v1/admin/tenants", json=body, headers=h)).status_code == 403
    root = await admin_headers(services, "root@example.com", superadmin=True)
    resp = await client.post("/v1/admin/tenants", json=body, headers=root)
    assert resp.status_code == 200
    again = await client.post("/v1/admin/tenants", json=body, headers=root)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "tenant.slug_taken"


async def test_last_owner_cannot_leave(client, services, tenant):
    tid = tenant.tenant_id
    h = await admin_headers(services, "own@example.com", tenant_id=tid, role="owner")
    me = (await client.get("/v1/admin/auth/me", headers=h)).json()["user"]
    resp = await client.patch(
        f"/v1/admin/tenants/{tid}/members/{me['id']}", json={"role": "admin"}, headers=h
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "member.last_owner"


async def test_api_key_is_shown_once_and_audited(client, services, tenant):
    tid = tenant.tenant_id
    h = await admin_headers(services, "adm@example.com", tenant_id=tid)
    resp = await client.post(f"/v1/admin/tenants/{tid}/api-keys", json={"title": "crm"}, headers=h)
    assert resp.status_code == 200
    raw = resp.json()["key"]
    assert raw.startswith("zk_")
    listed = (await client.get(f"/v1/admin/tenants/{tid}/api-keys", headers=h)).json()
    assert raw not in str(listed)
    # the new key works on the public API
    msg = await client.post(
        "/v1/messages",
        json={"contact": {"external_id": "c1"}, "text": "hi"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert msg.status_code != 401
    async with services.sm() as session:
        actions = list((await session.execute(select(AuditLog.action))).scalars())
    assert "api_key.create" in actions


async def test_endpoint_secret_is_write_only(client, services, tenant):
    tid = tenant.tenant_id
    h = await admin_headers(services, "adm@example.com", tenant_id=tid)
    resp = await client.post(
        f"/v1/admin/tenants/{tid}/endpoints",
        json={
            "name": "results_api",
            "url": "https://crm.example.com/hook",
            "headers": {"X-Api-Key": "very-secret-header"},
        },
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    generated = data["secret"]
    assert generated
    listed = (await client.get(f"/v1/admin/tenants/{tid}/endpoints", headers=h)).text
    assert generated not in listed
    assert "very-secret-header" not in listed
    assert "X-Api-Key" in listed
