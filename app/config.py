"""Settings, read from environment variables (12-factor). See README, "Configuration"."""

from __future__ import annotations

import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_KEY_LENGTH = 32
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
OWNER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Storage
    s3_bucket: str = Field(description="Private bucket that receives uploads")
    aws_region: str = Field(default="us-east-1", pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")
    s3_endpoint_url: str = Field(default="", description="Only for a local S3 mock; empty = real AWS")
    aws_profile: str = Field(default="", description="AWS CLI profile for local runs; empty = default chain")
    upload_prefix: str = Field(default="uploads", pattern=r"^[a-z0-9-]{1,32}$")

    # Limits
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=5 * 1024 * 1024 * 1024)
    upload_url_ttl_seconds: int = Field(default=300, ge=30, le=3_600)
    download_url_ttl_seconds: int = Field(default=60, ge=10, le=3_600)
    rate_limit_per_minute: int = Field(default=30, ge=1, le=100_000)
    ip_rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)

    # Who may call
    api_keys: dict[str, str] = Field(default_factory=dict, description='JSON: {"owner-id": "api-key"}')
    frontend_origin: str = Field(default="http://localhost:4200", pattern=r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")

    database_path: str = Field(default="data/uploads.db")
    enable_docs: bool = False
    log_level: str = "INFO"

    @field_validator("s3_bucket")
    @classmethod
    def _bucket_name(cls, name: str) -> str:
        if not BUCKET_PATTERN.match(name) or ".." in name:
            raise ValueError("not a valid S3 bucket name")
        if name.startswith("your-bucket"):
            raise ValueError("S3_BUCKET is still the placeholder from .env.example")
        return name

    @field_validator("api_keys")
    @classmethod
    def _strong_keys(cls, keys: dict[str, str]) -> dict[str, str]:
        for owner, key in keys.items():
            if not OWNER_PATTERN.match(owner):
                raise ValueError("owner ids must be 1-64 letters, digits, '_' or '-'")
            if len(key) < MIN_KEY_LENGTH:
                raise ValueError(f"API key for '{owner}' is shorter than {MIN_KEY_LENGTH} characters")
            if "replace-with" in key:
                raise ValueError(f"API key for '{owner}' is still the placeholder from .env.example")
        if len(set(keys.values())) != len(keys):
            raise ValueError("every owner needs a different API key")
        return keys
