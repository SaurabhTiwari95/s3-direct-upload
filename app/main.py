"""HTTP API. Run with:  uvicorn app.main:create_app --factory

Endpoints
  GET  /healthz                       liveness: the process is up
  GET  /readyz                        readiness: the database answers
  POST /v1/uploads                    check the file, pick the key, return a pre-signed POST
  GET  /v1/uploads                    your uploads, newest first (keyset paging)
  GET  /v1/uploads/{id}               one upload record
  POST /v1/uploads/{id}/complete      HEAD the object, confirm size, type and signature, record it
  GET  /v1/uploads/{id}/download      a short-lived pre-signed GET for a verified upload
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import __version__, files
from app.config import Settings
from app.db import COMPLETE, PENDING, REJECTED, Repository, Upload
from app.ratelimit import RateLimiter
from app.storage import Storage

log = logging.getLogger("uploads")

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
MAX_BODY_BYTES = 4_096  # the API only ever receives small JSON; files go straight to S3
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cross-Origin-Resource-Policy": "same-site",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, details: Any = None, headers: dict[str, str] | None = None):
        super().__init__(code)
        self.status, self.code, self.details, self.headers = status, code, details, headers or {}


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=512)

    filename: str
    content_type: Literal["application/pdf", "image/png", "image/jpeg"]
    size: int = Field(ge=1, description="Exact size in bytes; the bucket refuses any other size")

    @field_validator("filename")
    @classmethod
    def _filename(cls, value: str) -> str:
        return files.check_filename(value)


@dataclass
class Services:
    settings: Settings
    repo: Repository
    storage: Storage
    limiter: RateLimiter
    owners_by_digest: dict[str, str]
    clock: Any


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(u: Upload) -> dict[str, Any]:
    return {
        "id": u.id,
        "filename": u.filename,
        "content_type": u.content_type,
        "size": u.size,
        "status": u.status,
        "reason": u.reason,
        "created_at": _iso(u.created_at),
        "completed_at": _iso(u.completed_at),
    }


def _services(request: Request) -> Services:
    return request.app.state.services


async def authenticate(request: Request) -> str:
    """Per-IP limit, then the API key, then the per-owner limit. Returns the owner id."""
    svc = _services(request)
    ip = request.client.host if request.client else "unknown"
    if not svc.limiter.hit(f"ip:{ip}", svc.settings.ip_rate_limit_per_minute):
        raise ApiError(429, "rate_limited", headers={"Retry-After": "60"})
    key = request.headers.get("x-api-key", "")
    # Looked up by SHA-256 digest: equal-length hashes, and the plain keys are not kept after start-up.
    owner = svc.owners_by_digest.get(_digest(key)) if 1 <= len(key) <= 256 else None
    if owner is None:
        raise ApiError(401, "unauthorized")
    if not svc.limiter.hit(f"owner:{owner}", svc.settings.rate_limit_per_minute):
        raise ApiError(429, "rate_limited", headers={"Retry-After": "60"})
    return owner


async def _load(svc: Services, upload_id: str, owner: str) -> Upload:
    record = await asyncio.to_thread(svc.repo.get, upload_id, owner) if ID_PATTERN.match(upload_id) else None
    if record is None:
        raise ApiError(404, "not_found")  # also for someone else's upload: no existence oracle
    return record


def create_app(
    settings: Settings | None = None,
    repo: Repository | None = None,
    storage: Storage | None = None,
    clock=time.time,
) -> FastAPI:
    settings = settings or Settings()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        r = repo or Repository(settings.database_path)
        if not settings.api_keys:
            log.warning("API_KEYS is empty: every request to /v1 will be refused")
        app.state.services = Services(
            settings=settings,
            repo=r,
            storage=storage or Storage(settings),
            limiter=RateLimiter(),
            owners_by_digest={_digest(k): owner for owner, k in settings.api_keys.items()},
            clock=clock,
        )
        try:
            yield
        finally:
            r.close()

    app = FastAPI(
        title="s3-direct-upload",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex  # never trust a client-supplied id
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            response = JSONResponse({"error": "body_too_large", "request_id": request.state.request_id}, 413)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    # Added last, so it runs first: preflight requests are answered before anything else.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Api-Key"],
        expose_headers=["X-Request-Id", "Retry-After"],
        max_age=600,
    )

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        body: dict[str, Any] = {"error": exc.code, "request_id": getattr(request.state, "request_id", None)}
        if exc.details is not None:
            body["details"] = exc.details
        if exc.status in (401, 429):
            log.warning("refused %s %s: %s rid=%s", request.method, request.url.path, exc.code, body["request_id"])
        return JSONResponse(body, status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        # Field and reason only: the submitted value is never echoed back.
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p not in ("body", "query")) or "body", "error": e["msg"]}
            for e in exc.errors()
        ]
        rid = getattr(request.state, "request_id", None)
        return JSONResponse({"error": "invalid_request", "details": details, "request_id": rid}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None)
        log.exception("unhandled error rid=%s", rid)
        return JSONResponse({"error": "internal_error", "request_id": rid}, status_code=500)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request):
        ok = await asyncio.to_thread(_services(request).repo.ping)
        return JSONResponse({"status": "ready" if ok else "not_ready", "database": ok}, status_code=200 if ok else 503)

    @app.post("/v1/uploads", status_code=201)
    async def start_upload(body: UploadRequest, request: Request, owner: str = Depends(authenticate)):
        svc = _services(request)
        s = svc.settings
        if body.size > s.max_upload_bytes:
            raise ApiError(422, "file_too_large", {"max_bytes": s.max_upload_bytes})
        if not files.matches_type(body.filename, body.content_type):
            raise ApiError(422, "extension_does_not_match_type")

        now = int(svc.clock())
        upload_id = uuid.uuid4().hex
        ext = files.ALLOWED_TYPES[body.content_type].extensions[0]
        stamp = dt.datetime.fromtimestamp(now, dt.UTC)
        # The server picks the key. The user's file name is stored as data, never used as a path.
        key = f"{s.upload_prefix}/{stamp:%Y}/{stamp:%m}/{upload_id}.{ext}"
        post = svc.storage.presign_post(key, body.content_type, body.size, s.upload_url_ttl_seconds)
        await asyncio.to_thread(
            svc.repo.create,
            id=upload_id, owner=owner, object_key=key, filename=body.filename, content_type=body.content_type,
            size=body.size, created_at=now, expires_at=now + s.upload_url_ttl_seconds,
        )  # fmt: skip
        log.info("upload started id=%s owner=%s type=%s size=%s", upload_id, owner, body.content_type, body.size)
        return {
            "id": upload_id,
            "key": key,
            "upload": {"url": post["url"], "fields": post["fields"]},
            "expires_at": _iso(now + s.upload_url_ttl_seconds),
        }

    @app.get("/v1/uploads")
    async def list_uploads(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
        before: int | None = Query(default=None, ge=1),
        owner: str = Depends(authenticate),
    ):
        rows = await asyncio.to_thread(_services(request).repo.list, owner, limit + 1, before)
        page = rows[:limit]
        return {"items": [_record(u) for u in page], "next_before": page[-1].seq if len(rows) > limit else None}

    @app.get("/v1/uploads/{upload_id}")
    async def get_upload(upload_id: str, request: Request, owner: str = Depends(authenticate)):
        return _record(await _load(_services(request), upload_id, owner))

    @app.post("/v1/uploads/{upload_id}/complete")
    async def complete_upload(upload_id: str, request: Request, owner: str = Depends(authenticate)):
        svc = _services(request)
        record = await _load(svc, upload_id, owner)
        if record.status == COMPLETE:
            return _record(record)  # idempotent: a retried call gets the same answer
        if record.status == REJECTED:
            raise ApiError(409, "upload_rejected", {"reason": record.reason})

        now = int(svc.clock())
        reason = None
        try:
            info = await asyncio.to_thread(svc.storage.head, record.object_key)
            if info is None:
                if now > record.expires_at:
                    await asyncio.to_thread(svc.repo.finish, upload_id, REJECTED, "expired", now)
                    raise ApiError(410, "upload_expired")
                raise ApiError(409, "not_uploaded")
            if info.size != record.size:
                reason = "size_mismatch"
            elif info.content_type != record.content_type:
                reason = "type_mismatch"
            else:
                head = await asyncio.to_thread(svc.storage.first_bytes, record.object_key, files.SIGNATURE_BYTES)
                if not files.signature_ok(record.content_type, head):
                    reason = "signature_mismatch"
        except (ClientError, BotoCoreError):
            log.exception("storage check failed id=%s", upload_id)
            raise ApiError(502, "storage_unavailable") from None

        status = REJECTED if reason else COMPLETE
        await asyncio.to_thread(svc.repo.finish, upload_id, status, reason, now)
        log.info("upload %s id=%s owner=%s reason=%s", status, upload_id, owner, reason)
        if reason:
            # The object stays in the private bucket but is never served (see README, "Security model").
            raise ApiError(422, "upload_rejected", {"reason": reason})
        return _record(await _load(svc, upload_id, owner))

    @app.get("/v1/uploads/{upload_id}/download")
    async def download_upload(upload_id: str, request: Request, owner: str = Depends(authenticate)):
        svc = _services(request)
        record = await _load(svc, upload_id, owner)
        if record.status != COMPLETE:
            raise ApiError(409, "not_verified" if record.status == PENDING else "upload_rejected")
        ttl = svc.settings.download_url_ttl_seconds
        url = svc.storage.presign_get(
            record.object_key, files.content_disposition(record.filename), record.content_type, ttl
        )
        return {"url": url, "expires_at": _iso(int(svc.clock()) + ttl)}

    return app
