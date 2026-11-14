"""Run the whole flow on your laptop, with no AWS account: a local S3 mock (moto) plus the real API.

    python scripts/demo.py           # run the flow once and print what happens
    python scripts/demo.py --serve   # keep the mock S3 and the API (port 8000) running for the Angular app

What it does
  1. starts moto's S3 server on 127.0.0.1 and creates a private demo bucket
  2. starts the API (uvicorn) against it, with a fresh random API key
  3. asks for a pre-signed POST, uploads a sample PDF straight to "S3" with a multipart form POST,
     calls /complete, then downloads it with the pre-signed GET
  4. shows the checks catching two dishonest uploads

Nothing here talks to AWS: the credentials are dummies and every URL is 127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Dummy credentials: the mock accepts anything, and real AWS would refuse these.
os.environ.update(AWS_ACCESS_KEY_ID="demo", AWS_SECRET_ACCESS_KEY="demo", AWS_DEFAULT_REGION="us-east-1")
for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
    os.environ.pop(name, None)

import boto3  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from moto.server import ThreadedMotoServer  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

BUCKET = "demo-uploads-bucket"
HOST = "127.0.0.1"
SAMPLE_PDF = (
    b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n"
    b"% Acme Traders - sample invoice INV-0001\ntrailer << /Root 1 0 R >>\n%%EOF\n"
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def start_s3() -> tuple[ThreadedMotoServer, str]:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # the mock's access log
    port = free_port()
    server = ThreadedMotoServer(ip_address=HOST, port=port, verbose=False)
    server.start()
    endpoint = f"http://{HOST}:{port}"
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    # The same bucket settings docs/aws-setup.md applies in a real account.
    s3.put_public_access_block(
        Bucket=BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration=json.loads((ROOT / "aws/bucket-cors.json").read_text()))
    s3.put_bucket_lifecycle_configuration(
        Bucket=BUCKET, LifecycleConfiguration=json.loads((ROOT / "aws/lifecycle.json").read_text())
    )
    return server, endpoint


def start_api(endpoint: str, api_key: str, db_dir: str, port: int = 0) -> tuple[uvicorn.Server, str]:
    settings = Settings(
        _env_file=None,
        s3_bucket=BUCKET,
        s3_endpoint_url=endpoint,
        api_keys={"demo-user": api_key},
        database_path=str(Path(db_dir) / "uploads.db"),
        frontend_origin="http://localhost:4200",
        log_level="ERROR",
    )
    port = port or free_port()
    config = uvicorn.Config(create_app(settings), host=HOST, port=port, log_level="warning", server_header=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("API did not start")
        time.sleep(0.05)
    return server, f"http://{HOST}:{port}"


def short(url: str) -> str:
    return url.split("?", 1)[0] + ("?…" if "?" in url else "")


def upload_form(post: dict, body: bytes, filename: str, content_type: str) -> httpx.Response:
    """What the browser does: a multipart form POST, policy fields first and the file last."""
    return httpx.post(post["url"], data=post["fields"], files={"file": (filename, body, content_type)}, timeout=10)


def run_flow(api: httpx.Client) -> None:
    t0 = time.perf_counter()
    print("1. Ask the API for a pre-signed POST")
    r = api.post("/v1/uploads", json={"filename": "invoice-0001.pdf", "content_type": "application/pdf",
                                      "size": len(SAMPLE_PDF)})  # fmt: skip
    r.raise_for_status()
    start = r.json()
    print(f"   {r.status_code} id={start['id']}")
    print(f"   key={start['key']}  (picked by the server; the file name is only data)")
    print(f"   form fields: {', '.join(sorted(start['upload']['fields']))}")
    print(f"   expires_at={start['expires_at']}")

    print("2. Upload straight to S3 (the API never sees the bytes)")
    r = upload_form(start["upload"], SAMPLE_PDF, "invoice-0001.pdf", "application/pdf")
    print(f"   POST {start['upload']['url']} -> {r.status_code}")
    r.raise_for_status()

    print("3. Tell the API it's done: it HEADs the object and checks size, type and file signature")
    r = api.post(f"/v1/uploads/{start['id']}/complete")
    r.raise_for_status()
    print(f"   {r.status_code} {json.dumps(r.json())}")

    print("4. Download with a short-lived pre-signed GET")
    r = api.get(f"/v1/uploads/{start['id']}/download")
    r.raise_for_status()
    link = r.json()
    got = httpx.get(link["url"], timeout=10)
    print(f"   url={short(link['url'])} expires_at={link['expires_at']}")
    print(f"   GET -> {got.status_code}, {len(got.content)} bytes, same as uploaded: {got.content == SAMPLE_PDF}")
    print(f"   content-disposition: {got.headers.get('content-disposition')}")
    print(f"   whole flow: {(time.perf_counter() - t0) * 1000:.0f} ms")

    print("5. A client that lies about the size")
    r = api.post("/v1/uploads", json={"filename": "small.pdf", "content_type": "application/pdf", "size": 100})
    lie = r.json()
    r = upload_form(lie["upload"], b"%PDF-" + b"x" * 5_000, "small.pdf", "application/pdf")
    print(f"   upload 5,005 bytes against a 100-byte policy -> {r.status_code}"
          " (real S3 refuses this with 400 EntityTooLarge; the mock doesn't check POST policies)")  # fmt: skip
    r = api.post(f"/v1/uploads/{lie['id']}/complete")
    print(f"   complete -> {r.status_code} {r.json()['error']} {r.json()['details']}")

    print("6. An executable renamed to .pdf, with the right size and type")
    fake = b"MZ" + b"\x00" * 98
    r = api.post("/v1/uploads", json={"filename": "totally-a.pdf", "content_type": "application/pdf", "size": 100})
    exe = r.json()
    upload_form(exe["upload"], fake, "totally-a.pdf", "application/pdf").raise_for_status()
    r = api.post(f"/v1/uploads/{exe['id']}/complete")
    print(f"   complete -> {r.status_code} {r.json()['error']} {r.json()['details']}")
    r = api.get(f"/v1/uploads/{exe['id']}/download")
    print(f"   download -> {r.status_code} {r.json()['error']}")

    print("7. Someone without a valid key")
    r = api.get("/v1/uploads", headers={"X-Api-Key": "guess"})
    print(f"   {r.status_code} {r.json()['error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="keep running for the Angular app")
    parser.add_argument("--api-port", type=int, default=8000, help="API port in --serve mode (default 8000)")
    args = parser.parse_args(argv)

    api_key = secrets.token_urlsafe(32)
    s3_server, endpoint = start_s3()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as db_dir:
        api_server, base = start_api(endpoint, api_key, db_dir, args.api_port if args.serve else 0)
        try:
            print(f"Mock S3 at {endpoint} (bucket {BUCKET}), API at {base}\n", flush=True)
            if args.serve:
                print(f"API key for the Angular page: {api_key}")
                print("Press Ctrl+C to stop.", flush=True)
                while True:
                    time.sleep(1)
            with httpx.Client(base_url=base, headers={"X-Api-Key": api_key}, timeout=10) as api:
                run_flow(api)
        except KeyboardInterrupt:
            pass
        finally:
            api_server.should_exit = True
            time.sleep(0.3)
            s3_server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
