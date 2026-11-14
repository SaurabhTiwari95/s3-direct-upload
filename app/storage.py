"""S3 access: pre-signed POST and GET, and the checks after an upload.

boto3 is synchronous. Signing is local CPU work (no network call), so it runs inline; HEAD and the
ranged GET do wait on the network, so the API runs them in a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str


class Storage:
    def __init__(self, settings: Settings, client: Any | None = None):
        self.bucket = settings.s3_bucket
        if client is not None:
            self._client = client
            return
        session = boto3.session.Session(profile_name=settings.aws_profile or None)
        self._client = session.client(
            "s3",
            region_name=settings.aws_region,
            endpoint_url=settings.s3_endpoint_url or None,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=3,
                read_timeout=5,
                max_pool_connections=20,
            ),
        )

    def presign_post(self, key: str, content_type: str, size: int, expires_in: int) -> dict[str, Any]:
        """A POST policy the bucket itself enforces: this key, this exact type, this exact size."""
        return self._client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", size, size],
            ],
            ExpiresIn=expires_in,
        )

    def presign_get(self, key: str, disposition: str, content_type: str, expires_in: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": disposition,
                "ResponseContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    def head(self, key: str) -> ObjectInfo | None:
        """Size and type of the stored object, or None if it isn't there.

        HeadObject is authorised by s3:GetObject, so the API needs no extra permission for it.
        """
        try:
            meta = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return ObjectInfo(size=int(meta["ContentLength"]), content_type=meta.get("ContentType", ""))

    def first_bytes(self, key: str, count: int) -> bytes:
        """The first bytes of the object (a ranged GET), to check the file signature."""
        body = self._client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes=0-{count - 1}")["Body"]
        try:
            return body.read(count)
        finally:
            body.close()
