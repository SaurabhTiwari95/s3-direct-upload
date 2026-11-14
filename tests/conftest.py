"""Test fixtures. S3 is always moto's in-process mock: these tests never reach AWS."""

from __future__ import annotations

import os

# Fake credentials and region before boto3 is used anywhere, so nothing can reach a real account.
os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_SESSION_TOKEN="testing",
    AWS_DEFAULT_REGION="us-east-1",
)
os.environ.pop("AWS_PROFILE", None)

import boto3  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Repository  # noqa: E402
from app.main import create_app  # noqa: E402
from app.storage import Storage  # noqa: E402

BUCKET = "test-uploads-bucket"
ALICE_KEY = "alice-key-" + "a" * 32
BOB_KEY = "bob-key-" + "b" * 32
PDF = b"%PDF-1.7\n" + b"x" * 91  # 100 bytes
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 42  # 50 bytes


class Clock:
    """A clock the tests can move forward."""

    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        s3_bucket=BUCKET,
        api_keys={"alice": ALICE_KEY, "bob": BOB_KEY},
        frontend_origin="http://localhost:4200",
        max_upload_bytes=1_000,
        rate_limit_per_minute=1_000,
        ip_rate_limit_per_minute=1_000,
        database_path=":memory:",
    )


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def storage(settings, s3) -> Storage:
    return Storage(settings)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def app(settings, storage, clock):
    application = create_app(settings, repo=Repository(":memory:"), storage=storage, clock=clock)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def api(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://api.test", headers={"X-Api-Key": ALICE_KEY}
    ) as c:
        yield c


@pytest.fixture
def start(api):
    """POST /v1/uploads and return the JSON body."""

    async def _start(filename="invoice.pdf", content_type="application/pdf", size=len(PDF)):
        r = await api.post("/v1/uploads", json={"filename": filename, "content_type": content_type, "size": size})
        assert r.status_code == 201, r.text
        return r.json()

    return _start
