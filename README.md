# s3-direct-upload

[![CI](https://github.com/SaurabhTiwari95/s3-direct-upload/actions/workflows/ci.yml/badge.svg)](https://github.com/SaurabhTiwari95/s3-direct-upload/actions/workflows/ci.yml)
[![CodeQL](https://github.com/SaurabhTiwari95/s3-direct-upload/actions/workflows/codeql.yml/badge.svg)](https://github.com/SaurabhTiwari95/s3-direct-upload/actions/workflows/codeql.yml)

Browser-to-S3 uploads with pre-signed POST, a progress bar, and least-privilege IAM.

Uploading through an app server ties up workers and memory and pays for the bandwidth twice. Here
the browser sends the file **straight to a private S3 bucket**. The API only checks the request,
picks the object key and signs a short-lived POST policy; after the upload it verifies what
actually landed before anyone can download it.

```mermaid
sequenceDiagram
    participant B as Browser (Angular)
    participant A as API (FastAPI)
    participant S as S3 bucket (private)
    B->>A: POST /v1/uploads {filename, content_type, size}
    A->>A: check key, type, size, rate limit; pick key uploads/yyyy/mm/uuid.ext
    A-->>B: pre-signed POST: url + fields (exact key, type, size; expires in 5 min)
    B->>S: multipart form POST (fields, then the file), with progress
    S-->>B: 204 No Content
    B->>A: POST /v1/uploads/{id}/complete
    A->>S: HEAD object + read first 8 bytes
    A->>A: size, type and file signature match? record complete : rejected
    A-->>B: upload record
    B->>A: GET /v1/uploads/{id}/download
    A-->>B: pre-signed GET, valid 60 s
```

It implements the design in my architecture notes: note 04 and ADR 0004 in
[architecture-notes](https://github.com/SaurabhTiwari95/architecture-notes).

## Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Try it in two minutes, no AWS account](#try-it-in-two-minutes-no-aws-account)
- [Install](#install)
- [Run it against your AWS account](#run-it-against-your-aws-account)
- [Use the API](#use-the-api)
- [Configuration](#configuration)
- [Run the tests](#run-the-tests)
- [Troubleshooting](#troubleshooting)
- [Security model](#security-model)
- [Cost and clean-up](#cost-and-clean-up)
- [How it scales](#how-it-scales)
- [Performance](#performance)
- [Design decisions](#design-decisions)
- [Project layout](#project-layout)
- [What's next](#whats-next)

## Features

- **Pre-signed POST, not PUT:** the policy pins the exact key, the exact `Content-Type` and the exact
  size (`content-length-range`), so S3 itself refuses a 2 GB file claiming to be a 2 MB PDF.
- **Server-chosen keys** (`uploads/2026/11/<uuid>.pdf`): the user's file name is stored as data only.
- **Verify after upload:** `HEAD` for real size and type, plus a ranged read of the first bytes to
  check the file signature (`%PDF-`, PNG, JPEG). A renamed `.exe` is rejected and never served.
- **Short-lived links:** uploads expire in 5 minutes, downloads in 60 seconds, as attachments.
- **Least privilege:** the API can only `PutObject` and `GetObject` under `uploads/` in one bucket.
- **Private bucket:** Block Public Access, TLS-only policy, encryption at rest, CORS for one origin,
  lifecycle rules that abort incomplete multipart uploads and expire old files.
- **API hygiene:** API keys, per-IP and per-owner rate limits, owner-scoped records, Pydantic
  validation, CORS locked to the frontend, security headers, errors that never echo input,
  `/healthz` and `/readyz`.
- **Angular page** (standalone component, signals, zoneless): progress bar, clear error messages.
- **Runs offline:** a demo and the whole test suite use a local S3 mock ([moto](https://github.com/getmoto/moto)), never AWS.

## Prerequisites

| Tool | Version | Needed for | Get it |
|---|---|---|---|
| Python | 3.12 | API, demo, tests | <https://www.python.org/downloads/> |
| Node.js | 24 LTS (or 22.22.3+) | The Angular page only | <https://nodejs.org/en/download> |
| Git | any recent | Cloning | <https://git-scm.com/downloads> |
| AWS CLI | v2 | Only for the real-AWS setup | <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html> |
| Docker | 24+ | Optional: running the API as a container | <https://docs.docker.com/get-docker/> |

## Try it in two minutes, no AWS account

### Windows (PowerShell)

```powershell
git clone https://github.com/SaurabhTiwari95/s3-direct-upload.git
cd s3-direct-upload
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python scripts/demo.py
```

### macOS / Linux

```bash
git clone https://github.com/SaurabhTiwari95/s3-direct-upload.git
cd s3-direct-upload
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/demo.py
```

`demo.py` starts moto's S3 server and the real API (uvicorn) on `127.0.0.1`, then runs the whole
flow over HTTP, including a real multipart form POST to the pre-signed URL. Output from my laptop:

```text
Mock S3 at http://127.0.0.1:57159 (bucket demo-uploads-bucket), API at http://127.0.0.1:57164

1. Ask the API for a pre-signed POST
   201 id=de61ee3665a24f11baeff827f9fa28d6
   key=uploads/2026/11/de61ee3665a24f11baeff827f9fa28d6.pdf  (picked by the server; the file name is only data)
   form fields: Content-Type, key, policy, x-amz-algorithm, x-amz-credential, x-amz-date, x-amz-signature
   expires_at=2026-11-09T18:29:08Z
2. Upload straight to S3 (the API never sees the bytes)
   POST http://127.0.0.1:57159/demo-uploads-bucket -> 204
3. Tell the API it's done: it HEADs the object and checks size, type and file signature
   200 {"id": "de61ee3665a24f11baeff827f9fa28d6", "filename": "invoice-0001.pdf", "content_type": "application/pdf", "size": 183, "status": "complete", "reason": null, "created_at": "2026-11-09T18:24:08Z", "completed_at": "2026-11-09T18:24:08Z"}
4. Download with a short-lived pre-signed GET
   url=http://127.0.0.1:57159/demo-uploads-bucket/uploads/2026/11/de61ee3665a24f11baeff827f9fa28d6.pdf?… expires_at=2026-11-09T18:25:08Z
   GET -> 200, 183 bytes, same as uploaded: True
   content-disposition: attachment; filename="invoice-0001.pdf"; filename*=UTF-8''invoice-0001.pdf
   whole flow: 790 ms
5. A client that lies about the size
   upload 5,005 bytes against a 100-byte policy -> 204 (real S3 refuses this with 400 EntityTooLarge; the mock doesn't check POST policies)
   complete -> 422 upload_rejected {'reason': 'size_mismatch'}
6. An executable renamed to .pdf, with the right size and type
   complete -> 422 upload_rejected {'reason': 'signature_mismatch'}
   download -> 409 upload_rejected
7. Someone without a valid key
   401 unauthorized
```

Step 5 shows why the API verifies after upload: the mock doesn't enforce POST policies, so the
second line of defence catches it. Against real S3 the upload itself fails.

### The Angular page against the mock

```bash
python scripts/demo.py --serve
```

It keeps the mock S3 and the API running (API on `http://127.0.0.1:8000`) and prints an API key.
In a second terminal:

```bash
cd frontend
npm install
npm start
```

Open <http://localhost:4200>, paste the API key, choose a PDF, PNG or JPEG under 10 MB, and press
**Upload**. You'll see the progress bar, then "Uploaded and verified" with a download link.

> **Note:** the Angular app was **not built, run or tested on my build machine** (its Node version is
> too old for Angular 22). CI installs it, builds it and runs its unit tests headless (Vitest +
> jsdom) on Node 24 on every push. Screenshots to follow after the first run.

## Install

The API and the page install separately. The API needs Python only; the page needs Node.

### API — Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt        # requirements-dev.txt for tests and the demo
python scripts/make_env.py             # writes .env with a random API key and prints it
notepad .env                           # set S3_BUCKET (and AWS_REGION, AWS_PROFILE)
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

### API — macOS / Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # requirements-dev.txt for tests and the demo
python scripts/make_env.py             # writes .env with a random API key and prints it
nano .env                              # set S3_BUCKET (and AWS_REGION, AWS_PROFILE)
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Or as a container (one worker, non-root, data on a volume):

```bash
docker build -t s3-direct-upload .
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -e AWS_PROFILE= \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -v uploads-data:/srv/data s3-direct-upload
```

(In a container, pass credentials from your shell or, when deployed, use the platform's IAM role.)

### Frontend — Windows, macOS and Linux

```bash
cd frontend
npm install
npm start          # http://localhost:4200
npm run build      # production build in frontend/dist/
npm test           # unit tests (Vitest + jsdom), headless
```

The API address is in [frontend/src/environments/environment.ts](frontend/src/environments/environment.ts)
(`http://127.0.0.1:8000`). If you change where the page is served, set `FRONTEND_ORIGIN` on the API
and the origin in [aws/bucket-cors.json](aws/bucket-cors.json) to match.

## Run it against your AWS account

1. Follow [docs/aws-setup.md](docs/aws-setup.md): it creates the private bucket, the TLS-only
   policy, CORS, lifecycle rules and a least-privilege IAM user, with the AWS CLI, step by step.
2. Set `S3_BUCKET`, `AWS_REGION` and `AWS_PROFILE` in `.env`.
3. Start the API (above) and the page (`npm start`), and upload a file.

Credentials never go in `.env`: boto3 reads them from the AWS CLI profile locally, or from the IAM
role when deployed.

## Use the API

Every `/v1` call needs the `X-Api-Key` header. Replace `$KEY` with the key from `make_env.py` (or
from `demo.py --serve`).

### 1. Start an upload

```bash
curl -s -X POST http://127.0.0.1:8000/v1/uploads \
  -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"filename": "invoice-0001.pdf", "content_type": "application/pdf", "size": 183}'
```

```json
{"id": "de61ee3665a24f11baeff827f9fa28d6",
 "key": "uploads/2026/11/de61ee3665a24f11baeff827f9fa28d6.pdf",
 "upload": {"url": "https://YOUR-BUCKET-NAME.s3.amazonaws.com/",
            "fields": {"Content-Type": "application/pdf", "key": "uploads/2026/11/de61...pdf",
                       "x-amz-algorithm": "AWS4-HMAC-SHA256", "x-amz-credential": "...",
                       "x-amz-date": "...", "policy": "...", "x-amz-signature": "..."}},
 "expires_at": "2026-11-09T18:29:08Z"}
```

### 2. Upload to S3

Send every field from `upload.fields`, then the file **last**, as `multipart/form-data`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$URL" \
  -F "Content-Type=application/pdf" -F "key=$OBJECT_KEY" -F "policy=$POLICY" \
  -F "x-amz-algorithm=AWS4-HMAC-SHA256" -F "x-amz-credential=$CRED" -F "x-amz-date=$DATE" \
  -F "x-amz-signature=$SIG" -F "file=@invoice-0001.pdf"
```

```text
204
```

### 3. Complete and download

```bash
curl -s -X POST http://127.0.0.1:8000/v1/uploads/$ID/complete -H "X-Api-Key: $KEY"
curl -s http://127.0.0.1:8000/v1/uploads/$ID/download -H "X-Api-Key: $KEY"
```

```json
{"id": "de61...", "filename": "invoice-0001.pdf", "content_type": "application/pdf", "size": 183,
 "status": "complete", "reason": null, "created_at": "2026-11-09T18:24:08Z", "completed_at": "2026-11-09T18:24:08Z"}
{"url": "https://YOUR-BUCKET-NAME.s3.amazonaws.com/uploads/2026/11/de61...pdf?X-Amz-...", "expires_at": "2026-11-09T18:25:08Z"}
```

### The same in Python

```python
import httpx

API, KEY = "http://127.0.0.1:8000", "<your API key>"
data = open("invoice-0001.pdf", "rb").read()

with httpx.Client(base_url=API, headers={"X-Api-Key": KEY}) as api:
    start = api.post(
        "/v1/uploads", json={"filename": "invoice-0001.pdf", "content_type": "application/pdf", "size": len(data)}
    ).json()
    post = start["upload"]
    r = httpx.post(post["url"], data=post["fields"], files={"file": ("invoice-0001.pdf", data, "application/pdf")})
    print(r.status_code)  # 204
    print(api.post(f"/v1/uploads/{start['id']}/complete").json()["status"])  # complete
    print(api.get(f"/v1/uploads/{start['id']}/download").json()["url"][:60])
```

### Endpoints

| Method and path | What it does |
|---|---|
| `GET /healthz` | Liveness: the process is up |
| `GET /readyz` | Readiness: the database answers (`503` if not) |
| `POST /v1/uploads` | Validate name, type and size; return a pre-signed POST (`201`) |
| `GET /v1/uploads?limit=20&before=<cursor>` | Your uploads, newest first; `next_before` is the next page's cursor |
| `GET /v1/uploads/{id}` | One of your upload records |
| `POST /v1/uploads/{id}/complete` | Verify the stored object and record it; safe to retry |
| `GET /v1/uploads/{id}/download` | Pre-signed GET for a verified upload |

### Errors

| Status | `error` | Meaning |
|---|---|---|
| 401 | `unauthorized` | Missing or wrong `X-Api-Key` |
| 404 | `not_found` | No such upload, or it belongs to someone else |
| 409 | `not_uploaded` | `/complete` called before the file reached S3 |
| 409 | `not_verified` / `upload_rejected` | Download asked for an upload that isn't verified, or was rejected |
| 410 | `upload_expired` | The upload link expired and no file arrived |
| 413 | `body_too_large` | Request body over 4 KB (the API only takes small JSON) |
| 422 | `invalid_request` | `details` names the field and why, never the value sent |
| 422 | `file_too_large` / `extension_does_not_match_type` | Declared size over the limit, or `.png` declared as a PDF |
| 422 | `upload_rejected` | `details.reason`: `size_mismatch`, `type_mismatch`, `signature_mismatch` or `expired` |
| 429 | `rate_limited` | Over the per-minute limit; see `Retry-After` |
| 502 | `storage_unavailable` | S3 couldn't be checked; logged with the `request_id` |
| 500 | `internal_error` | Logged with the `request_id`; no internals in the response |

## Configuration

All settings are environment variables (or `.env`).

| Variable | Purpose | Default | Required |
|---|---|---|---|
| `S3_BUCKET` | The private bucket | — | Yes |
| `AWS_REGION` | The bucket's region | `us-east-1` | No |
| `AWS_PROFILE` | AWS CLI profile for local runs; empty = default credential chain (role, env) | empty | No |
| `S3_ENDPOINT_URL` | Only for a local S3 mock; empty = real AWS | empty | No |
| `UPLOAD_PREFIX` | Key prefix; must match the IAM policy | `uploads` | No |
| `API_KEYS` | JSON `{"owner-id": "api-key"}`; keys 32+ characters, all different | `{}` | Yes |
| `FRONTEND_ORIGIN` | The one origin CORS allows | `http://localhost:4200` | No |
| `MAX_UPLOAD_BYTES` | Largest file | `10485760` (10 MB) | No |
| `UPLOAD_URL_TTL_SECONDS` | Pre-signed POST lifetime | `300` | No |
| `DOWNLOAD_URL_TTL_SECONDS` | Pre-signed GET lifetime | `60` | No |
| `RATE_LIMIT_PER_MINUTE` | Requests per owner per minute | `30` | No |
| `IP_RATE_LIMIT_PER_MINUTE` | Requests per client IP per minute, checked before the key | `120` | No |
| `DATABASE_PATH` | SQLite file for upload records | `data/uploads.db` | No |
| `ENABLE_DOCS` | Serve `/docs` and `/openapi.json` | `false` | No |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`… | `INFO` | No |

Allowed types are PDF, PNG and JPEG, defined in [app/files.py](app/files.py) with their extensions
and file signatures. Placeholder or weak values (`your-bucket-name`, `replace-with…`, keys under 32
characters, a `*` origin) are refused at start-up.

## Run the tests

```bash
pytest
```

```text
120 passed ... TOTAL ... 99%
```

S3 is moto's in-process mock in every test: they never reach AWS. One test runs the whole demo over
real HTTP (`-m "not slow"` skips it). Lint, the dependency audit and the pre-publish scan:

```bash
ruff check . && ruff format --check .
pip-audit -r requirements-dev.txt
python scripts/prepublish_check.py
```

Frontend (Node 24):

```bash
cd frontend && npm test && npm run format:check
```

CI runs all of this on every push, plus `npm audit`, the production build, CodeQL for Python and
TypeScript, gitleaks, a Docker build that must run as non-root, and Markdown and link checks.

## Troubleshooting

| Problem | Fix |
|---|---|
| API won't start: `S3_BUCKET ... still the placeholder` or `API key ... placeholder` | Run `python scripts/make_env.py`, then set `S3_BUCKET` in `.env` |
| Page says "Could not reach the server … CORS" | API not running on `127.0.0.1:8000`, or `FRONTEND_ORIGIN` doesn't match the page's origin exactly (scheme, host and port) |
| Upload to S3 fails in the browser with a CORS error | The bucket's CORS doesn't list the page's origin: update [aws/bucket-cors.json](aws/bucket-cors.json) and re-run `put-bucket-cors` |
| S3 answers `403 AccessDenied` on upload | The link expired (5 minutes), or the API's IAM policy names another bucket or prefix than `S3_BUCKET` / `UPLOAD_PREFIX` |
| S3 answers `400 EntityTooLarge` / `EntityTooSmall` | The file's size differs from the size declared when the upload started: start again with the real size |
| `422 upload_rejected` with `signature_mismatch` | The file's content isn't really a PDF/PNG/JPEG (for example a renamed file) |
| `ProfileNotFound` at start-up | `AWS_PROFILE` names a profile that doesn't exist: run `aws configure --profile s3-direct-upload` |
| `npm install` fails with an engine error | Angular 22 needs Node 22.22.3+ or 24.15+: `node --version` |
| `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once |

## Security model

| Threat | What stops it |
|---|---|
| Huge file or wrong type sent to S3 | POST policy: exact `Content-Type` and `content-length-range` equal to the declared size; S3 refuses anything else |
| Client lies, or a policy is weaker than intended | `/complete` checks real size and type with `HEAD`, and the file signature with a ranged read; mismatches are rejected and never served |
| Overwriting someone else's file, path tricks | The server picks the key (`uploads/yyyy/mm/<uuid>.ext`) and the policy pins it; names with `/`, `\` or control characters are refused |
| Old links reused | Uploads expire in 5 minutes, downloads in 60 seconds |
| Public exposure | Block Public Access, ACLs disabled, no public policy; every read is a pre-signed GET |
| Stolen API credentials | The IAM policy allows only `PutObject`/`GetObject` under `uploads/` of one bucket: no list, no delete, no other buckets |
| Snooping on the wire | Bucket policy denies non-TLS and TLS below 1.2 |
| Another user's data | Every record is scoped to its owner; someone else's id answers `404` |
| Brute force and abuse | Per-IP limit before the key check, per-owner limit after it; 4 KB body limit |
| Content served inline from the bucket | Downloads force `Content-Disposition: attachment` with a sanitised name |
| Leaky errors | Generic messages with a request id; input is never echoed; logs hold ids, not file names |

What it doesn't do yet (see [SECURITY.md](SECURITY.md)): real user sign-in (API keys stand in for
it), malware scanning, and immediate deletion of rejected files.

## Cost and clean-up

- For a demo this costs cents a month, often nothing; [docs/aws-setup.md](docs/aws-setup.md#cost)
  has the Free Tier note and list prices.
- **Lifecycle rules** ([aws/lifecycle.json](aws/lifecycle.json)): incomplete multipart uploads
  are aborted after **1 day** (abandoned parts are otherwise billed forever), and files under
  `uploads/` move to Standard-IA after 30 days and are deleted after 90.
- Files never pass through the API, so it can run on the smallest instance or container.
- Set a zero-spend or small monthly **budget alert** before you start.
- **Teardown:** [docs/aws-setup.md](docs/aws-setup.md#teardown) removes the bucket, the IAM user,
  its keys and the policy.

## How it scales

- **Upload volume doesn't touch the API.** Signing a POST policy takes about 0.2 ms of CPU and no
  network call; the bytes go to S3, which scales by itself.
- **S3 request rates** scale per prefix; the date and UUID in each key spread the load.
- **Large files:** above about 100 MB, switch to multipart upload with a pre-signed URL per part
  (and keep the abort-incomplete lifecycle rule).

What breaks first, and the fix:

| Load | First bottleneck | Fix |
|---|---|---|
| 10× | SQLite's single writer and the in-memory rate limiter tie the API to one instance | Move records to PostgreSQL (same queries; add Alembic migrations and a connection pool) and rate limits to Redis; then run several instances |
| 10× | `/complete` waits on two S3 calls per upload | Already async (worker threads); raise the boto3 pool size, or let S3 event notifications (or EventBridge) drive verification instead of the browser |
| 100× | Verification and malware scanning in the request path | S3 event → SQS queue → workers verify and scan; the page polls `GET /v1/uploads/{id}` |
| 100× | Rate limiting per instance | Enforce at the edge (API Gateway usage plans, or ALB + AWS WAF) |

## Performance

Measured on my Windows laptop, **in-process** (client and API share one Python thread; S3 is
moto's in-process mock and SQLite is in memory). It measures the API's own cost for
`POST /v1/uploads`: validation, key choice, signing the POST policy and inserting the record.

```bash
python scripts/loadtest.py                                # 2,000 requests, 20 at a time
python scripts/loadtest.py --requests 5000 --concurrency 50
```

| Run | Throughput | p50 | p95 | p99 |
|---|---|---|---|---|
| 2,000 requests, 20 at a time | 517 req/s | 36 ms | 53 ms | 102 ms |
| 2,000 requests, 20 at a time | 502 req/s | 37 ms | 58 ms | 103 ms |
| 5,000 requests, 50 at a time | 424 req/s | 98 ms | 188 ms | 244 ms |

Latency here is mostly queueing: all callers share one thread. Timed on their own, signing a POST
policy takes about 0.22 ms, a pre-signed GET about 0.39 ms, and the SQLite insert about 0.03 ms.

**Profiled before optimising.** The first run gave about 370 req/s. A profile showed the biggest
cost was not the API: httpx's in-process test transport tried a failing `import sniffio` on every
request, scanning the file system each time. Switching that off in the load generator gave the
numbers above. Running the SQLite calls inline instead of in a worker thread was about 7% faster,
but it would block the event loop on a slow disk, so the thread stays.

## Design decisions

- **Pre-signed POST rather than PUT.** A PUT URL can't limit size; a POST policy can, and pins the
  type and key too. (My original project brief said PUT; the design notes chose POST for this reason.)
- **Exact size, not a range.** The client declares the size, so the policy allows exactly that many
  bytes. Combined with the `HEAD` check, the record and the object always agree.
- **Verify, then trust.** The browser's word ("done") only triggers checks. The first 8 bytes are
  read to catch renamed files cheaply; full malware scanning belongs in an async pipeline.
- **No delete permission.** Rejected files stay private and unserved until the lifecycle rule
  removes them, rather than giving the internet-facing API `s3:DeleteObject`.
- **SQLite for records** keeps the demo dependency-free; queries are parameterised and indexed
  (owner + sequence, checked with `EXPLAIN QUERY PLAN`), and keyset paging moves to PostgreSQL unchanged.
- **API keys for the demo.** Simple to run and test; documented as a stand-in for real sign-in.
  Keys are looked up by SHA-256 digest and only accepted in a header, never a URL.
- **Angular, minimal:** one standalone component, signals, no router, no forms module, no UI kit.
  The default XHR backend is used because `fetch` can't report upload progress.

## Project layout

```text
app/
  main.py       HTTP API: endpoints, auth, rate limits, CORS, security headers, errors
  storage.py    S3: pre-signed POST and GET, HEAD and ranged read
  files.py      allowed types, file-name rules, file signatures, Content-Disposition
  db.py         SQLite upload records, migrations, keyset paging
  ratelimit.py  fixed-window rate limiter
  config.py     settings from environment variables
frontend/       Angular 22 page (standalone, zoneless) and its unit tests
aws/            IAM policy, bucket policy, CORS, lifecycle and encryption JSON
docs/           AWS setup guide, release checklist
scripts/        demo (offline flow), loadtest, make_env, prepublish_check
tests/          unit, API (moto) and end-to-end demo tests
```

## What's next

- Multipart uploads with per-part pre-signed URLs for files over 100 MB.
- Verification driven by S3 event notifications and a queue, plus malware scanning.
- OIDC sign-in instead of API keys; per-tenant key prefixes (`uploads/{tenant}/...`).
- PostgreSQL and Redis for running several API instances.

---

AI-assisted (Claude Code); design and review by me.

Licence: [MIT](LICENSE). Security: [SECURITY.md](SECURITY.md).
