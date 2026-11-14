"""What may be uploaded: an allow-list of types, file-name rules and file signatures."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class FileType:
    content_type: str
    extensions: tuple[str, ...]  # the first one is used in the object key
    magic: tuple[bytes, ...]  # accepted first bytes of the file


ALLOWED_TYPES: dict[str, FileType] = {
    t.content_type: t
    for t in (
        FileType("application/pdf", ("pdf",), (b"%PDF-",)),
        FileType("image/png", ("png",), (b"\x89PNG\r\n\x1a\n",)),
        FileType("image/jpeg", ("jpg", "jpeg"), (b"\xff\xd8\xff",)),
    )
}
SIGNATURE_BYTES = max(len(m) for t in ALLOWED_TYPES.values() for m in t.magic)

_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f/\\]")


def check_filename(name: str) -> str:
    """Return the cleaned display name, or raise ValueError. The name is data only, never a path."""
    name = unicodedata.normalize("NFC", name).strip()
    if not 1 <= len(name) <= 255:
        raise ValueError("must be 1-255 characters")
    if _UNSAFE_NAME.search(name):
        raise ValueError("must not contain control characters or path separators")
    if name in {".", ".."} or "." not in name.strip("."):
        raise ValueError("must have a file extension")
    return name


def extension_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower()


def matches_type(name: str, content_type: str) -> bool:
    file_type = ALLOWED_TYPES.get(content_type)
    return file_type is not None and extension_of(name) in file_type.extensions


def signature_ok(content_type: str, first_bytes: bytes) -> bool:
    file_type = ALLOWED_TYPES.get(content_type)
    return file_type is not None and any(first_bytes.startswith(m) for m in file_type.magic)


def ascii_filename(name: str) -> str:
    """A safe file name for the Content-Disposition header of a download."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", ascii_name).strip() or "download"
    return ascii_name[:120]


def content_disposition(name: str) -> str:
    """`attachment` with an ASCII fallback and the full UTF-8 name (RFC 6266)."""
    from urllib.parse import quote

    return f"attachment; filename=\"{ascii_filename(name)}\"; filename*=UTF-8''{quote(name, safe='')}"
