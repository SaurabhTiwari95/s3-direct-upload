from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import demo  # noqa: E402
import loadtest  # noqa: E402
import make_env  # noqa: E402


def test_make_env_writes_a_random_key_and_refuses_to_overwrite(tmp_path, capsys):
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    assert make_env.main([], root=tmp_path) == 0
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    line = next(x for x in env.splitlines() if x.startswith("API_KEYS="))
    keys = json.loads(line.split("=", 1)[1])
    assert list(keys) == ["demo-user"] and len(keys["demo-user"]) >= 32 and "replace-with" not in env
    assert keys["demo-user"] in capsys.readouterr().out
    assert make_env.main([], root=tmp_path) == 1
    assert make_env.main(["--force"], root=tmp_path) == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") != env


def test_aws_json_files_parse():
    for name in ("iam-policy.json", "bucket-policy.json", "bucket-cors.json", "lifecycle.json", "encryption.json"):
        json.loads((ROOT / "aws" / name).read_text(encoding="utf-8"))
    policy = json.loads((ROOT / "aws/iam-policy.json").read_text(encoding="utf-8"))
    (statement,) = policy["Statement"]
    assert sorted(statement["Action"]) == ["s3:GetObject", "s3:PutObject"]
    assert statement["Resource"].endswith("/uploads/*")


@pytest.mark.slow
def test_demo_runs_the_whole_flow_over_http(capsys):
    """Real HTTP end to end: uvicorn + moto's S3 server on 127.0.0.1, a multipart form POST upload."""
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    assert "POST http://127.0.0.1:" in out and "-> 204" in out
    assert '"status": "complete"' in out
    assert "same as uploaded: True" in out
    assert "422 upload_rejected {'reason': 'size_mismatch'}" in out
    assert "422 upload_rejected {'reason': 'signature_mismatch'}" in out
    assert "401 unauthorized" in out


def test_loadtest_small_run(capsys):
    assert loadtest.main(["--requests", "40", "--concurrency", "4"]) == 0
    assert "POST /v1/uploads x40" in capsys.readouterr().out
