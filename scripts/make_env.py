"""Create .env from .env.example with a fresh random API key.

    python scripts/make_env.py            # refuses to overwrite an existing .env
    python scripts/make_env.py --force

The key is printed once so you can paste it into the Angular page. .env is git-ignored.
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build_env(example: str, owner: str = "demo-user") -> tuple[str, str]:
    api_key = secrets.token_urlsafe(32)
    values = {"API_KEYS": json.dumps({owner: api_key})}
    lines = []
    for line in example.splitlines():
        key = line.split("=", 1)[0].strip()
        if not line.lstrip().startswith("#") and key in values:
            line = f"{key}={values[key]}"
        lines.append(line)
    return "\n".join(lines) + "\n", api_key


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    argv = sys.argv[1:] if argv is None else argv
    target = root / ".env"
    if target.exists() and "--force" not in argv:
        print(".env already exists; run with --force to replace it", file=sys.stderr)
        return 1
    text, api_key = build_env((root / ".env.example").read_text(encoding="utf-8"))
    target.write_text(text, encoding="utf-8")
    print("wrote .env with a fresh random API key (it is git-ignored; never commit it)")
    print("now set S3_BUCKET in .env to your bucket's name")
    print(f"API key for the Angular page: {api_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
