from __future__ import annotations

import base64
import calendar
import json
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from botocore.exceptions import ClientError

from app.db import Repository
from app.main import create_app
from tests.conftest import ALICE_KEY, BOB_KEY, BUCKET, PDF, PNG


def policy_of(fields: dict) -> dict:
    return json.loads(base64.b64decode(fields["policy"]))


def put(s3, key, body, content_type):
    """What the browser's form POST does, done through the S3 API (moto in-process)."""
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=content_type)


# --- health -------------------------------------------------------------------------------------


async def test_health_and_ready(api):
    assert (await api.get("/healthz")).json() == {"status": "ok"}
    r = await api.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready", "database": True}


async def test_ready_fails_when_database_is_down(app, api):
    app.state.services.repo._conn.close()
    r = await api.get("/readyz")
    assert r.status_code == 503 and r.json()["database"] is False


async def test_security_headers_and_request_id(api):
    r = await api.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["x-request-id"])


async def test_docs_are_off_by_default(api):
    assert (await api.get("/docs")).status_code == 404
    assert (await api.get("/openapi.json")).status_code == 404


# --- CORS ---------------------------------------------------------------------------------------


async def test_cors_allows_only_the_frontend_origin(api):
    ok = await api.options(
        "/v1/uploads",
        headers={"Origin": "http://localhost:4200", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type,x-api-key"},
    )  # fmt: skip
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "http://localhost:4200"

    evil = await api.options(
        "/v1/uploads", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"}
    )
    assert evil.status_code == 400
    assert "access-control-allow-origin" not in evil.headers


# --- auth and rate limits -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers", [{}, {"X-Api-Key": ""}, {"X-Api-Key": "wrong-" + "x" * 40}, {"X-Api-Key": "k" * 300}]
)
async def test_requests_without_a_valid_key_are_refused(api, headers):
    r = await api.get("/v1/uploads", headers={**headers, "X-Api-Key": headers.get("X-Api-Key", "")})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


async def test_owner_rate_limit(settings, storage, clock):
    settings.rate_limit_per_minute = 3
    app = create_app(settings, repo=Repository(":memory:"), storage=storage, clock=clock)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", headers={"X-Api-Key": ALICE_KEY}) as c:
            codes = [(await c.get("/v1/uploads")).status_code for _ in range(4)]
            assert codes == [200, 200, 200, 429]
            r = await c.get("/v1/uploads")
            assert r.headers["retry-after"] == "60" and r.json()["error"] == "rate_limited"
            # another owner has their own budget
            assert (await c.get("/v1/uploads", headers={"X-Api-Key": BOB_KEY})).status_code == 200


async def test_ip_rate_limit_applies_before_auth(settings, storage, clock):
    settings.ip_rate_limit_per_minute = 2
    app = create_app(settings, repo=Repository(":memory:"), storage=storage, clock=clock)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            codes = [(await c.get("/v1/uploads", headers={"X-Api-Key": "guess"})).status_code for _ in range(3)]
            assert codes == [401, 401, 429]


# --- start an upload: validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "body, field",
    [
        ({"filename": "a.pdf", "content_type": "application/pdf"}, "size"),
        ({"filename": "a.pdf", "content_type": "application/pdf", "size": 0}, "size"),
        ({"filename": "a.pdf", "content_type": "application/pdf", "size": -5}, "size"),
        ({"filename": "a.exe", "content_type": "application/x-msdownload", "size": 10}, "content_type"),
        ({"filename": "a.svg", "content_type": "image/svg+xml", "size": 10}, "content_type"),
        ({"filename": "../../etc/passwd.pdf", "content_type": "application/pdf", "size": 10}, "filename"),
        ({"filename": "a\\b.pdf", "content_type": "application/pdf", "size": 10}, "filename"),
        ({"filename": "bad\x00.pdf", "content_type": "application/pdf", "size": 10}, "filename"),
        ({"filename": "noextension", "content_type": "application/pdf", "size": 10}, "filename"),
        ({"filename": "", "content_type": "application/pdf", "size": 10}, "filename"),
        ({"filename": "a" * 300 + ".pdf", "content_type": "application/pdf", "size": 10}, "filename"),
        ({"filename": "a.pdf", "content_type": "application/pdf", "size": 10, "key": "x"}, "key"),
    ],
)
async def test_invalid_requests_are_refused_without_echoing_input(api, body, field):
    r = await api.post("/v1/uploads", json=body)
    assert r.status_code == 422
    data = r.json()
    assert data["error"] == "invalid_request"
    assert field in [d["field"] for d in data["details"]]
    assert "passwd" not in r.text and "x-msdownload" not in r.text.replace("content_type", "")


async def test_file_over_the_limit_is_refused(api):
    r = await api.post("/v1/uploads", json={"filename": "big.pdf", "content_type": "application/pdf", "size": 1_001})
    assert r.status_code == 422
    assert r.json()["error"] == "file_too_large" and r.json()["details"] == {"max_bytes": 1_000}


async def test_extension_must_match_type(api):
    r = await api.post("/v1/uploads", json={"filename": "photo.png", "content_type": "application/pdf", "size": 10})
    assert r.status_code == 422 and r.json()["error"] == "extension_does_not_match_type"


async def test_body_size_is_limited(api):
    r = await api.post("/v1/uploads", content=b"{" + b" " * 5_000 + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"] == "body_too_large"


async def test_malformed_json_is_a_clean_422(api):
    r = await api.post("/v1/uploads", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"] == "invalid_request"


# --- start an upload: the pre-signed POST -------------------------------------------------------


async def test_presigned_post_conditions(start, clock):
    data = await start("Q3 report.pdf", "application/pdf", 100)
    key = data["key"]
    # server-chosen key: prefix / year / month / uuid . extension; the user's name is not in it
    assert re.fullmatch(r"uploads/2027/01/[0-9a-f]{32}\.pdf", key)
    assert data["id"] in key and "report" not in key
    assert data["upload"]["url"].endswith(BUCKET) or BUCKET in data["upload"]["url"]

    fields = data["upload"]["fields"]
    assert fields["key"] == key
    assert fields["Content-Type"] == "application/pdf"
    assert fields["x-amz-algorithm"] == "AWS4-HMAC-SHA256"  # SigV4

    conditions = policy_of(fields)["conditions"]
    assert {"bucket": BUCKET} in conditions
    assert {"key": key} in conditions
    assert {"Content-Type": "application/pdf"} in conditions
    assert ["content-length-range", 100, 100] in conditions  # the bucket refuses any other size


async def test_presigned_post_expires_in_five_minutes(start):
    before = time.time()
    data = await start()
    expiration = policy_of(data["upload"]["fields"])["expiration"]
    expires = calendar.timegm(time.strptime(expiration[:19], "%Y-%m-%dT%H:%M:%S"))
    assert before + 299 <= expires <= time.time() + 301
    assert data["expires_at"] == "2027-01-15T08:05:00Z"  # injected clock + 300 s


async def test_jpeg_uses_the_canonical_extension(start):
    data = await start("holiday.JPEG", "image/jpeg", 10)
    assert data["key"].endswith(".jpg")


# --- complete -----------------------------------------------------------------------------------


async def test_complete_happy_path_then_download(api, start, s3):
    data = await start("invoice.pdf", "application/pdf", len(PDF))
    put(s3, data["key"], PDF, "application/pdf")

    r = await api.post(f"/v1/uploads/{data['id']}/complete")
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "complete" and rec["size"] == len(PDF) and rec["filename"] == "invoice.pdf"
    assert rec["completed_at"] == "2027-01-15T08:00:00Z"

    # idempotent
    again = await api.post(f"/v1/uploads/{data['id']}/complete")
    assert again.status_code == 200 and again.json() == rec

    r = await api.get(f"/v1/uploads/{data['id']}/download")
    assert r.status_code == 200
    url = urlparse(r.json()["url"])
    q = parse_qs(url.query)
    assert q["X-Amz-Expires"] == ["60"]
    assert q["response-content-disposition"][0].startswith('attachment; filename="invoice.pdf"')
    assert q["response-content-type"] == ["application/pdf"]
    assert r.json()["expires_at"] == "2027-01-15T08:01:00Z"
    # the URL really fetches the object (moto in-process)
    assert s3.get_object(Bucket=BUCKET, Key=data["key"])["Body"].read() == PDF


async def test_complete_png(api, start, s3):
    data = await start("logo.png", "image/png", len(PNG))
    put(s3, data["key"], PNG, "image/png")
    assert (await api.post(f"/v1/uploads/{data['id']}/complete")).json()["status"] == "complete"


@pytest.mark.parametrize(
    "body, content_type, reason",
    [
        (PDF + b"extra", "application/pdf", "size_mismatch"),  # bigger than declared
        (PDF[:50], "application/pdf", "size_mismatch"),  # smaller than declared
        (PDF, "image/png", "type_mismatch"),  # stored with another Content-Type
        (b"MZ" + b"\x00" * 98, "application/pdf", "signature_mismatch"),  # an .exe dressed as a PDF
    ],
)
async def test_complete_rejects_what_does_not_match(api, start, s3, body, content_type, reason):
    data = await start("invoice.pdf", "application/pdf", len(PDF))
    put(s3, data["key"], body, content_type)

    r = await api.post(f"/v1/uploads/{data['id']}/complete")
    assert r.status_code == 422
    assert r.json()["error"] == "upload_rejected" and r.json()["details"] == {"reason": reason}

    # rejected for good, and never downloadable
    again = await api.post(f"/v1/uploads/{data['id']}/complete")
    assert again.status_code == 409 and again.json()["details"] == {"reason": reason}
    dl = await api.get(f"/v1/uploads/{data['id']}/download")
    assert dl.status_code == 409 and dl.json()["error"] == "upload_rejected"
    assert (await api.get(f"/v1/uploads/{data['id']}")).json()["status"] == "rejected"


async def test_complete_before_upload_says_not_uploaded(api, start):
    data = await start()
    r = await api.post(f"/v1/uploads/{data['id']}/complete")
    assert r.status_code == 409 and r.json()["error"] == "not_uploaded"
    assert (await api.get(f"/v1/uploads/{data['id']}")).json()["status"] == "pending"


async def test_complete_after_expiry_without_object_is_gone(api, start, clock):
    data = await start()
    clock.now += 301
    r = await api.post(f"/v1/uploads/{data['id']}/complete")
    assert r.status_code == 410 and r.json()["error"] == "upload_expired"
    rec = (await api.get(f"/v1/uploads/{data['id']}")).json()
    assert rec["status"] == "rejected" and rec["reason"] == "expired"


async def test_download_before_complete_is_refused(api, start, s3):
    data = await start()
    put(s3, data["key"], PDF, "application/pdf")
    r = await api.get(f"/v1/uploads/{data['id']}/download")
    assert r.status_code == 409 and r.json()["error"] == "not_verified"


async def test_storage_errors_are_not_leaked(api, start, app, monkeypatch):
    data = await start()

    def boom(key):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "secret detail"}}, "HeadObject")

    monkeypatch.setattr(app.state.services.storage, "head", boom)
    r = await api.post(f"/v1/uploads/{data['id']}/complete")
    assert r.status_code == 502 and r.json()["error"] == "storage_unavailable"
    assert "secret" not in r.text and "AccessDenied" not in r.text


async def test_unexpected_errors_are_generic(settings, storage, clock, monkeypatch):
    app = create_app(settings, repo=Repository(":memory:"), storage=storage, clock=clock)
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.services.repo, "list", lambda *a: 1 / 0)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", headers={"X-Api-Key": ALICE_KEY}) as c:
            r = await c.get("/v1/uploads")
            assert r.status_code == 500
            assert r.json()["error"] == "internal_error" and "ZeroDivision" not in r.text


# --- ownership, lookups and listing --------------------------------------------------------------


async def test_other_owners_cannot_see_complete_or_download(api, start, s3):
    data = await start()
    put(s3, data["key"], PDF, "application/pdf")
    bob = {"X-Api-Key": BOB_KEY}
    for method, path in [
        ("GET", f"/v1/uploads/{data['id']}"),
        ("POST", f"/v1/uploads/{data['id']}/complete"),
        ("GET", f"/v1/uploads/{data['id']}/download"),
    ]:
        r = await api.request(method, path, headers=bob)
        assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert (await api.get("/v1/uploads", headers=bob)).json()["items"] == []


@pytest.mark.parametrize("upload_id", ["nope", "0" * 32, "A" * 32, "' OR 1=1 --"])
async def test_unknown_or_malformed_ids_are_404(api, upload_id):
    r = await api.get(f"/v1/uploads/{upload_id}")
    assert r.status_code == 404


async def test_list_is_paged_newest_first(api, start):
    ids = [(await start(f"f{i}.pdf"))["id"] for i in range(5)]
    page1 = (await api.get("/v1/uploads", params={"limit": 2})).json()
    assert [i["id"] for i in page1["items"]] == ids[::-1][:2]
    page2 = (await api.get("/v1/uploads", params={"limit": 2, "before": page1["next_before"]})).json()
    assert [i["id"] for i in page2["items"]] == ids[::-1][2:4]
    page3 = (await api.get("/v1/uploads", params={"limit": 2, "before": page2["next_before"]})).json()
    assert [i["id"] for i in page3["items"]] == ids[:1] and page3["next_before"] is None


async def test_list_limit_is_bounded(api):
    assert (await api.get("/v1/uploads", params={"limit": 101})).status_code == 422
    assert (await api.get("/v1/uploads", params={"limit": 0})).status_code == 422
