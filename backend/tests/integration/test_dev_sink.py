import pytest

from app.results.delivery import IDEMPOTENCY_HEADER, SIGNATURE_HEADER

pytestmark = pytest.mark.db


async def test_sink_stores_lists_and_clears(client):
    headers = {IDEMPOTENCY_HEADER: "d1", SIGNATURE_HEADER: "sha256=abc", "X-Other": "x"}
    resp = await client.post("/dev/sink/crm", json={"seq": 1}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    await client.post("/dev/sink/crm", json={"seq": 2})
    await client.post("/dev/sink/other", json={"seq": 9})

    items = (await client.get("/dev/sink/crm")).json()["items"]
    assert [i["body"] for i in items] == [{"seq": 2}, {"seq": 1}]
    assert items[1]["headers"][IDEMPOTENCY_HEADER] == "d1"
    assert items[1]["headers"][SIGNATURE_HEADER] == "sha256=abc"
    assert "X-Other" not in items[1]["headers"]

    assert (await client.delete("/dev/sink/crm")).json() == {"deleted": 2}
    assert (await client.get("/dev/sink/crm")).json()["items"] == []
    assert len((await client.get("/dev/sink/other")).json()["items"]) == 1


async def test_sink_answers_with_the_asked_status_and_still_stores(client):
    resp = await client.post("/dev/sink/crm?respond_status=503", json={"a": 1})
    assert resp.status_code == 503
    assert resp.json()["ok"] is False
    assert len((await client.get("/dev/sink/crm")).json()["items"]) == 1


async def test_sink_keeps_a_non_json_body(client):
    await client.post("/dev/sink/crm", content=b"plain text")
    await client.post("/dev/sink/crm", json=[1, 2])
    bodies = [i["body"] for i in (await client.get("/dev/sink/crm")).json()["items"]]
    assert bodies == [{"_value": [1, 2]}, {"_raw": "plain text"}]


async def test_sink_is_off_in_prod(client, services):
    services.settings = services.settings.model_copy(update={"env": "prod"})
    assert (await client.post("/dev/sink/crm", json={})).status_code == 404
    assert (await client.get("/dev/sink/crm")).status_code == 404
