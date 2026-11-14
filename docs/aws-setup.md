# AWS setup (your personal account)

Step by step with the AWS CLI. Everything here stays inside one bucket, one IAM policy and one IAM
user, and the [teardown](#teardown) at the end removes all of it.

Use a **personal** AWS account that you control. Never a work or client account.

## Contents

- [Before you start](#before-you-start)
- [1. Choose names](#1-choose-names)
- [2. Create the private bucket](#2-create-the-private-bucket)
- [3. Lock the bucket down](#3-lock-the-bucket-down)
- [4. CORS: only the app's origin](#4-cors-only-the-apps-origin)
- [5. Lifecycle: abort incomplete uploads, expire old files](#5-lifecycle-abort-incomplete-uploads-expire-old-files)
- [6. Least-privilege identity for the API](#6-least-privilege-identity-for-the-api)
- [7. Point the API at the bucket](#7-point-the-api-at-the-bucket)
- [8. Check it](#8-check-it)
- [Cost](#cost)
- [Teardown](#teardown)

## Before you start

- AWS CLI v2: <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>
- A CLI profile with admin rights in your personal account, used only for this setup
  (for example IAM Identity Center: `aws configure sso --profile personal-admin`).
- A budget alert, so a mistake can't surprise you: **Billing and Cost Management → Budgets →
  Create budget → Zero spend budget** (or a monthly budget of a few dollars).

Each command below fits on one line, so it runs the same in Bash and PowerShell. Only setting
variables and replacing placeholders differ; both versions are shown.

## 1. Choose names

Bucket names are global, so add something random.

```bash
# Bash (macOS / Linux / Git Bash)
export AWS_PROFILE=personal-admin
BUCKET=s3-direct-upload-$(openssl rand -hex 4)
REGION=us-east-1
echo $BUCKET
```

```powershell
# PowerShell
$env:AWS_PROFILE = "personal-admin"
$BUCKET = "s3-direct-upload-" + -join ((48..57) + (97..102) | Get-Random -Count 8 | ForEach-Object { [char]$_ })
$REGION = "us-east-1"
$BUCKET
```

Pick the region closest to your users; the rest of this guide uses `$REGION`.

## 2. Create the private bucket

In `us-east-1`:

```bash
aws s3api create-bucket --bucket $BUCKET --region $REGION
```

In any other region:

```bash
aws s3api create-bucket --bucket $BUCKET --region $REGION --create-bucket-configuration LocationConstraint=$REGION
```

## 3. Lock the bucket down

Block every kind of public access, and turn ACLs off so only policies decide access:

```bash
aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-ownership-controls --bucket $BUCKET --ownership-controls "Rules=[{ObjectOwnership=BucketOwnerEnforced}]"
```

Encryption at rest with S3-managed keys (SSE-S3). New buckets have this already; setting it makes
it explicit:

```bash
aws s3api put-bucket-encryption --bucket $BUCKET --server-side-encryption-configuration file://aws/encryption.json
```

Refuse any request that isn't over TLS 1.2+ ([aws/bucket-policy.json](../aws/bucket-policy.json)).
Fill in the bucket name first:

```bash
# Bash
sed "s/YOUR-BUCKET-NAME/$BUCKET/g" aws/bucket-policy.json > bucket-policy.local.json
```

```powershell
# PowerShell
(Get-Content aws/bucket-policy.json) -replace 'YOUR-BUCKET-NAME', $BUCKET | Set-Content bucket-policy.local.json
```

```bash
aws s3api put-bucket-policy --bucket $BUCKET --policy file://bucket-policy.local.json
```

The `*.local.json` files hold your bucket name; delete them when you're done (they're only on your
computer; don't commit them).

## 4. CORS: only the app's origin

[aws/bucket-cors.json](../aws/bucket-cors.json) allows `POST` and `GET` from
`http://localhost:4200` only, the Angular dev server. When you deploy the page, replace that origin
with your real one (for example `https://uploads.example.com`); never use `*`.

```bash
aws s3api put-bucket-cors --bucket $BUCKET --cors-configuration file://aws/bucket-cors.json
```

## 5. Lifecycle: abort incomplete uploads, expire old files

[aws/lifecycle.json](../aws/lifecycle.json) has two rules:

| Rule | What it does | Why |
|---|---|---|
| `abort-incomplete-multipart-uploads` | Deletes the parts of any multipart upload not finished within 1 day | Abandoned parts are invisible in the console and billed until removed |
| `uploads-to-infrequent-access-then-expire` | Moves `uploads/` objects to Standard-IA after 30 days and deletes them after 90 | Keeps a demo bucket from growing. For real data, retention is a business decision: change or remove the expiry |

Standard-IA charges a 30-day minimum and a 128 KB minimum per object, so for many tiny files you
may prefer to skip the transition and keep only the expiry.

```bash
aws s3api put-bucket-lifecycle-configuration --bucket $BUCKET --lifecycle-configuration file://aws/lifecycle.json
```

## 6. Least-privilege identity for the API

The API needs exactly two actions on one prefix ([aws/iam-policy.json](../aws/iam-policy.json)):

- `s3:PutObject` on `uploads/*`: the pre-signed POST is signed with the API's credentials, so the
  browser's upload is authorised by this permission.
- `s3:GetObject` on `uploads/*`: pre-signed downloads, the `HEAD` check after upload (HeadObject is
  authorised by `s3:GetObject`) and the ranged read of the first bytes.

No `ListBucket`, no `DeleteObject`, nothing outside `uploads/`.

```bash
# Bash
sed "s/YOUR-BUCKET-NAME/$BUCKET/g" aws/iam-policy.json > iam-policy.local.json
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
```

```powershell
# PowerShell
(Get-Content aws/iam-policy.json) -replace 'YOUR-BUCKET-NAME', $BUCKET | Set-Content iam-policy.local.json
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
```

```bash
aws iam create-policy --policy-name s3-direct-upload-api --policy-document file://iam-policy.local.json
aws iam create-user --user-name s3-direct-upload-api
aws iam attach-user-policy --user-name s3-direct-upload-api --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/s3-direct-upload-api
aws iam create-access-key --user-name s3-direct-upload-api
```

`create-access-key` prints an access key id and secret **once**. Put them straight into a CLI
profile and nowhere else (not in `.env`, not in the repo):

```bash
aws configure --profile s3-direct-upload
```

Enter the key id, the secret, your region, and `json`.

**Access keys are for running the API on your laptop only.** When you deploy it (ECS, App Runner,
Lambda, EC2), attach the same policy to the service's IAM **role** instead and delete the user's
keys: roles get short-lived credentials automatically.

## 7. Point the API at the bucket

```bash
python scripts/make_env.py
```

Then set these in `.env`:

```text
S3_BUCKET=<your bucket name, the value of $BUCKET>
AWS_REGION=<your region>
AWS_PROFILE=s3-direct-upload
```

Start the API and the Angular page as described in the [README](../README.md#run-it-against-your-aws-account).

## 8. Check it

After an upload from the page:

```bash
aws s3api head-object --bucket $BUCKET --key uploads/<yyyy>/<mm>/<id>.pdf --profile s3-direct-upload
```

The bucket must refuse anonymous access (expect `403`). In PowerShell, type `curl.exe` instead of
`curl` and `NUL` instead of `/dev/null`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://$BUCKET.s3.$REGION.amazonaws.com/uploads/"
```

And the API's identity must not be able to list or delete (expect `AccessDenied` for both):

```bash
aws s3 ls s3://$BUCKET --profile s3-direct-upload
aws s3api delete-object --bucket $BUCKET --key uploads/anything --profile s3-direct-upload
```

## Cost

For a demo this costs cents a month, often nothing:

- **Free Tier:** what you get depends on when your account was created (AWS changed the Free Tier in
  2025: older accounts have 12 months of 5 GB S3 Standard plus request allowances; newer accounts
  get credits). Check **Billing → Free Tier** in your account.
- **List prices** (S3 Standard, `us-east-1`, at the time of writing; see
  <https://aws.amazon.com/s3/pricing/>): storage about $0.023 per GB-month, `PUT`/`POST` about
  $0.005 per 1,000 requests, `GET`/`HEAD` about $0.0004 per 1,000 requests.
- **Data transfer:** uploads into S3 are free; downloads to the internet are billed per GB after a
  monthly free allowance.
- What keeps it small: the 10 MB upload limit, the lifecycle rules (abandoned multipart parts
  deleted after a day, files deleted after 90 days), no versioning, and the budget alert.

## Teardown

Removes everything this guide created. Deleting objects and the bucket **cannot be undone**.

```bash
aws s3 rm s3://$BUCKET --recursive
aws s3api delete-bucket --bucket $BUCKET
aws iam list-access-keys --user-name s3-direct-upload-api
aws iam delete-access-key --user-name s3-direct-upload-api --access-key-id <AccessKeyId from the line above>
aws iam detach-user-policy --user-name s3-direct-upload-api --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/s3-direct-upload-api
aws iam delete-user --user-name s3-direct-upload-api
aws iam delete-policy --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/s3-direct-upload-api
aws configure set aws_access_key_id "" --profile s3-direct-upload
aws configure set aws_secret_access_key "" --profile s3-direct-upload
```

Then delete `bucket-policy.local.json` and `iam-policy.local.json`, and remove the
`[s3-direct-upload]` sections from `~/.aws/credentials` and `~/.aws/config`.
