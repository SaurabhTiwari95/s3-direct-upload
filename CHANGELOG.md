# Changelog

## 0.1.0 (2026-11-09)

- API (FastAPI): pre-signed POST with exact key, type and size conditions and a 5-minute expiry;
  server-chosen keys; `/complete` verifies size, type and file signature; 60-second pre-signed downloads.
- Upload records in SQLite with keyset paging; API keys, per-IP and per-owner rate limits,
  CORS locked to the frontend origin, security headers, generic errors.
- Angular 22 page (standalone, zoneless, signals): upload straight to S3 with a progress bar.
- AWS files: least-privilege IAM policy, TLS-only bucket policy, CORS, lifecycle, encryption;
  step-by-step CLI setup and teardown for a personal account.
- Offline demo against a local S3 mock (moto), load test, tests with moto (no AWS), Docker image,
  CI (lint, tests, frontend build and tests, Docker, docs, pip-audit, npm audit, gitleaks, CodeQL).
