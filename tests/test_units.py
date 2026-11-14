from __future__ import annotations

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from app import files
from app.config import Settings
from app.db import COMPLETE, MIGRATIONS, PENDING, REJECTED, Repository
from app.ratelimit import RateLimiter
from tests.conftest import BUCKET, PDF

GOOD_KEY = "k" * 40


# --- config ---------------------------------------------------------------------------------------


def make(**kw):
    return Settings(_env_file=None, **{"s3_bucket": "my-demo-bucket", **kw})


def test_defaults_are_safe():
    s = make()
    assert s.upload_url_ttl_seconds == 300
    assert s.download_url_ttl_seconds == 60
    assert s.max_upload_bytes == 10 * 1024 * 1024
    assert s.enable_docs is False
    assert s.api_keys == {}


@pytest.mark.parametrize(
    "kw",
    [
        {"s3_bucket": "Bad_Bucket"},
        {"s3_bucket": "ab"},
        {"s3_bucket": "a..b"},
        {"s3_bucket": "your-bucket-name"},
        {"api_keys": {"alice": "short"}},
        {"api_keys": {"alice": "replace-with-a-long-random-key-of-32-plus-chars"}},
        {"api_keys": {"bad owner!": GOOD_KEY}},
        {"api_keys": {"alice": GOOD_KEY, "bob": GOOD_KEY}},
        {"frontend_origin": "*"},
        {"frontend_origin": "http://localhost:4200/path"},
        {"aws_region": "mars"},
        {"upload_url_ttl_seconds": 7_200},
        {"upload_prefix": "../etc"},
    ],
)
def test_unsafe_settings_are_refused(kw):
    with pytest.raises(ValidationError):
        make(**kw)


def test_bucket_is_required(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "env-bucket")
    monkeypatch.setenv("API_KEYS", '{"alice": "' + GOOD_KEY + '"}')
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "2048")
    s = Settings(_env_file=None)
    assert (s.s3_bucket, s.api_keys, s.max_upload_bytes) == ("env-bucket", {"alice": GOOD_KEY}, 2048)


# --- file rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a.pdf", "Q3 report (final).PDF", "  spaced.png  ", "résumé.pdf", ".hidden.jpg"])
def test_good_filenames(name):
    assert files.check_filename(name) == name.strip()


@pytest.mark.parametrize("name", ["", " ", "a/b.pdf", "a\\b.pdf", "x\n.pdf", "noext", ".", "..", "...", "a" * 256])
def test_bad_filenames(name):
    with pytest.raises(ValueError):
        files.check_filename(name)


@pytest.mark.parametrize(
    "name, ctype, ok",
    [
        ("a.pdf", "application/pdf", True),
        ("a.PDF", "application/pdf", True),
        ("a.jpeg", "image/jpeg", True),
        ("a.jpg", "image/jpeg", True),
        ("a.png", "application/pdf", False),
        ("a.pdf", "text/html", False),
    ],
)
def test_extension_matches_type(name, ctype, ok):
    assert files.matches_type(name, ctype) is ok


def test_signatures():
    assert files.signature_ok("application/pdf", PDF[: files.SIGNATURE_BYTES])
    assert files.signature_ok("image/jpeg", b"\xff\xd8\xff\xe0rest")
    assert not files.signature_ok("image/png", b"\x89PNX\r\n\x1a\n")
    assert not files.signature_ok("text/plain", b"%PDF-")
    assert files.SIGNATURE_BYTES == 8


def test_content_disposition_is_header_safe():
    value = files.content_disposition('evil"\r\nSet-Cookie: x=1;résumé.pdf')
    assert "\r" not in value and "\n" not in value
    assert value.startswith('attachment; filename="evil___Set-Cookie_ x_1_resume.pdf"')
    assert "filename*=UTF-8''evil%22%0D%0ASet-Cookie%3A%20x%3D1%3Br%C3%A9sum%C3%A9.pdf" in value
    assert files.ascii_filename("日本.pdf") == ".pdf"
    assert files.ascii_filename("日本") == "download"


# --- rate limiter ---------------------------------------------------------------------------------


def test_rate_limiter_window_resets():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert [limiter.hit("a", 2) for _ in range(3)] == [True, True, False]
    assert limiter.hit("b", 2)
    now[0] = 60.0
    assert limiter.hit("a", 2)


def test_rate_limiter_memory_is_bounded():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0], max_keys=10)
    for i in range(11):
        limiter.hit(f"k{i}", 5)
    now[0] = 120.0
    limiter.hit("fresh", 5)
    assert len(limiter._windows) == 1


# --- repository -----------------------------------------------------------------------------------


def new(repo, i="a" * 32, owner="alice", key="uploads/2027/01/x.pdf"):
    return repo.create(id=i, owner=owner, object_key=key, filename="x.pdf", content_type="application/pdf",
                       size=10, created_at=100, expires_at=400)  # fmt: skip


def test_repository_round_trip_and_finish_once():
    repo = Repository(":memory:")
    assert repo.schema_version() == len(MIGRATIONS)
    rec = new(repo)
    assert rec.status == PENDING and rec.seq == 1
    assert repo.get(rec.id, "bob") is None
    assert repo.finish(rec.id, COMPLETE, None, 200) is True
    assert repo.finish(rec.id, REJECTED, "late", 300) is False  # only a pending record can finish
    assert repo.get(rec.id, "alice").status == COMPLETE
    repo.close()


def test_repository_keys_are_unique():
    import sqlite3

    repo = Repository(":memory:")
    new(repo)
    with pytest.raises(sqlite3.IntegrityError):
        new(repo, i="b" * 32)  # same object key


def test_repository_file_migrates_once(tmp_path):
    path = tmp_path / "sub" / "uploads.db"
    repo = Repository(str(path))
    new(repo)
    repo.close()
    again = Repository(str(path))  # re-open: no migration re-run, data kept
    assert again.schema_version() == 1
    assert again.get("a" * 32, "alice") is not None
    again.close()


def test_list_uses_the_owner_index():
    repo = Repository(":memory:")
    plan = repo._conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM uploads WHERE owner = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
        ("alice", 10, 5),
    ).fetchall()
    assert any("uploads_owner_seq" in row[-1] for row in plan)


# --- storage --------------------------------------------------------------------------------------


def test_head_reraises_errors_other_than_not_found(storage, monkeypatch):
    def denied(**kw):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")

    monkeypatch.setattr(storage._client, "head_object", denied)
    with pytest.raises(ClientError):
        storage.head("uploads/x.pdf")


def test_head_and_first_bytes(storage, s3):
    s3.put_object(Bucket=BUCKET, Key="uploads/a.pdf", Body=PDF, ContentType="application/pdf")
    info = storage.head("uploads/a.pdf")
    assert (info.size, info.content_type) == (len(PDF), "application/pdf")
    assert storage.first_bytes("uploads/a.pdf", 8) == PDF[:8]
    assert storage.head("uploads/missing.pdf") is None
