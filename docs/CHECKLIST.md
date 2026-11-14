# Release checklist

The same checklist every one of my projects meets before publishing. Ticked only when verified;
anything checked only by CI (not yet run) says so.

Last checked: 2026-11-09

## A. Installation and usage

- [x] What it is, with a diagram (Mermaid sequence diagram)
- [x] Prerequisites with versions and links
- [x] Install steps for Windows (PowerShell) and macOS/Linux, for the API and the frontend
- [x] Usage with expected output: offline demo (real output pasted), curl and Python examples, endpoint and error tables
- [x] One command for tests (`pytest`; `npm test` for the frontend)
- [x] Configuration table for every environment variable
- [x] Troubleshooting
- [x] Security model, cost and clean-up, how it scales, performance, design decisions, what's next
- [x] AI-assisted line
- [x] Relative links and in-page anchors checked locally (0 broken); external links checked in CI by lychee
- [x] The offline demo (`python scripts/demo.py`) runs end to end on the build machine: moto S3 server + uvicorn, real HTTP form POST
- [ ] **Frontend not built, run or tested on the build machine** (Node 12 there; Angular 22 needs Node 22.22.3+/24.15+). CI installs, builds and runs its unit tests on Node 24
- [ ] Screenshots of the page: after the first real run
- [ ] Real AWS run of [docs/aws-setup.md](aws-setup.md): not done (no AWS calls from the build machine). The JSON files were applied to the moto mock (CORS, lifecycle, public access block) without errors
- [ ] Docker install steps: not run on the build machine (no Docker there). CI builds the image, checks it runs as uid 10001, and smoke-tests `/healthz`, `/readyz` and auth

## B. No private data

- [x] Sample data only: "Acme Traders" sample invoice in the demo; placeholders `YOUR-BUCKET-NAME`, `<ACCOUNT_ID>`-style variables
- [x] No employer, product or people names; no real hosts, IPs, account ids, ARNs, bucket names or keys
- [x] No work code, names, counts or costs
- [x] Pre-publish check: 0 hits locally against the private block-list, over the API, docs, AWS files and the Angular sources; it also runs in CI
- [ ] gitleaks: CI only (not installed on the build machine)

## C. Secure by default

- [x] Secrets only from the environment; `.env` git-ignored; `scripts/make_env.py` generates the API key; AWS credentials come from a CLI profile or IAM role, never `.env`
- [x] Placeholder or weak settings refused at start-up (tested)
- [x] Input validated at the edge: Pydantic models, allow-listed types, size limit, file-name rules, 4 KB body limit
- [x] Parameterised SQL only
- [x] Least privilege: IAM `s3:PutObject`/`s3:GetObject` on `uploads/*` of one bucket; private bucket, Block Public Access, TLS-only policy, encryption at rest
- [x] Rate limits (per IP and per owner); secure headers; CORS locked to the frontend origin (tested); bucket CORS to the app origin; docs off by default
- [x] Errors never echo input or internals (tested); logs carry ids, not file names or keys
- [x] Dependencies pinned to current releases; **pip-audit: no known vulnerabilities** (run 2026-11-09)
- [ ] `npm audit --audit-level=high`: CI only (needs Node 22+)
- [ ] Frontend lock file: none committed yet (couldn't be generated with the build machine's Node). CI uses `npm install` and uploads the generated `package-lock.json` as an artifact; commit it, and CI switches to `npm ci`
- [x] Dependabot (pip, npm, Actions, Docker); CodeQL workflow for Python and JavaScript/TypeScript; gitleaks in CI
- [x] Docker: multi-stage, slim base, non-root (uid 10001)
- [x] `SECURITY.md`
- [ ] CodeQL results: first run happens on GitHub after the push
- [ ] GitHub Actions pinned to commit SHAs: pinned to major versions; Dependabot keeps them current
- [ ] Repo settings (on GitHub after the push): secret scanning + push protection, private vulnerability reporting, branch protection on `main` requiring CI

## D. Scalable

- [x] Stateless request handling; all config from the environment (12-factor)
- [ ] One instance only as shipped: SQLite and the in-memory rate limiter. The README's "How it scales" gives the PostgreSQL + Redis path
- [x] Indexes on every lookup (unique id, unique key, owner + sequence; plan checked in a test); migrations tracked with `PRAGMA user_version`
- [ ] Alembic: not used (SQLite demo with one table); planned with the move to PostgreSQL
- [x] Keyset paging on the list endpoint, capped at 100
- [x] `/healthz` and `/readyz`
- [x] "How it scales" section: first bottlenecks at 10× and 100× and their fixes
- N/A Background jobs: requests are short; the scaling section moves verification and scanning to a queue at 100×

## E. Optimised

- [x] Async API; S3 and SQLite calls in worker threads; signing done inline (local CPU only)
- [x] boto3 client created once, connection pool sized, timeouts and retries set
- N/A Caching: no repeated reads worth caching (every link is fresh by design)
- [x] No N+1: one query per call
- [x] Multi-stage Docker image
- [x] Profiled before optimising: the biggest cost was in the load generator (httpx's per-request `import sniffio` attempt), not the API; numbers in the README
- [x] Load test with numbers in the README (in-process)

## F. Repo basics

- [x] Tests: 120 passed locally, 99% coverage (gate 80% in CI), including an end-to-end demo over HTTP
- [x] Lint: ruff check and ruff format clean
- [ ] Frontend unit tests (14 specs) and Prettier check: CI only
- [x] Markdown: core rules checked locally; full markdownlint runs in CI
- [x] YAML and JSON files parse
- [x] `.gitignore` (incl. `node_modules`, `dist`, `.angular`), `LICENSE` (MIT), `CHANGELOG.md`
- N/A `docker-compose.yml`: no database server needed (SQLite)
