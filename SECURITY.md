# Security policy

This is a personal demonstration project. Please report a security problem privately:
**Security** tab → **Report a vulnerability**. Please don't open a public issue.
I aim to reply within 7 days.

## What's in place

- Files never pass through the API: the browser uploads straight to a **private** bucket with a
  pre-signed POST that expires in 5 minutes
- The POST policy pins the exact object key, the exact `Content-Type` and the exact size
  (`content-length-range`), so S3 itself refuses anything else
- The server picks every object key; the user's file name is stored as data, never used as a path
- After upload, the API checks the stored object's size and type (`HEAD`) and its first bytes
  (file signature) before the record is marked complete; anything else is rejected and never served
- Downloads are pre-signed GETs valid for 60 seconds, with `Content-Disposition: attachment`
- Least-privilege IAM: `s3:PutObject` and `s3:GetObject` on `uploads/*` of one bucket, nothing else
- Bucket: Block Public Access, ACLs disabled, encryption at rest (SSE-S3), TLS-only bucket policy,
  CORS limited to the app's origin, lifecycle rule aborting incomplete multipart uploads
- API: API keys (32+ characters, compared by SHA-256 digest), per-IP and per-owner rate limits,
  owner-scoped records (someone else's upload is a 404), Pydantic validation, small body limit,
  CORS locked to one origin, security headers, generic error messages that never echo input
- Pinned dependencies, pip-audit and npm audit in CI, Dependabot, CodeQL (Python and
  TypeScript), gitleaks, a non-root container

## Not in scope for this demo

- **User authentication.** API keys stand in for real sign-in. In production, put the API behind
  your identity provider (OIDC / session cookies) and derive the owner from the signed-in user.
- **Malware scanning.** The signature check stops renamed files, not malicious PDFs or images. Scan
  asynchronously after upload (for example Amazon GuardDuty Malware Protection for S3) and
  quarantine on failure.
- **Deleting rejected objects.** The API's role can't delete (least privilege), so rejected files
  stay in the private bucket, are never served, and are removed by the lifecycle expiry. Add a
  separate cleanup job with `s3:DeleteObject` if they must go sooner.
- **TLS termination** for the API: put a load balancer or reverse proxy with TLS in front.
