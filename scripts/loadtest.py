"""Measure the API's own cost per request, in-process, with S3 mocked (no network, no AWS).

    python scripts/loadtest.py                       # 2,000 requests, 20 at a time
    python scripts/loadtest.py --requests 5000 --concurrency 50
    python scripts/loadtest.py --profile             # cProfile the run, top 15 by cumulative time

It drives POST /v1/uploads (validate, pick the key, sign the POST policy, insert the record), which
is the call every upload makes. /complete waits on S3 (HEAD + a ranged GET), so in production its
time is S3's latency, not this code; it isn't measured here.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.update(AWS_ACCESS_KEY_ID="demo", AWS_SECRET_ACCESS_KEY="demo", AWS_DEFAULT_REGION="us-east-1")
os.environ.pop("AWS_PROFILE", None)

import boto3  # noqa: E402
import httpx  # noqa: E402
import httpx._transports.asgi  # noqa: E402
from moto import mock_aws  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Repository  # noqa: E402
from app.main import create_app  # noqa: E402
from app.storage import Storage  # noqa: E402

KEY = "load-test-key-" + "x" * 32

# Found by profiling: httpx's in-process transport tries `import sniffio` on every request to detect
# trio. Without sniffio installed, each failed import scans the file system, which cost more than the
# API itself on Windows. It's the load generator's cost, not the API's, so it's switched off here.
httpx._transports.asgi.is_running_trio = lambda: False


async def run(requests: int, concurrency: int) -> dict[str, float]:
    settings = Settings(
        _env_file=None,
        s3_bucket="load-test-bucket",
        api_keys={"load": KEY},
        rate_limit_per_minute=100_000,
        ip_rate_limit_per_minute=100_000,
        database_path=":memory:",
        log_level="WARNING",
    )
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=settings.s3_bucket)
    app = create_app(settings, repo=Repository(":memory:"), storage=Storage(settings))
    latencies: list[float] = []
    body = {"filename": "invoice.pdf", "content_type": "application/pdf", "size": 1234}

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", headers={"X-Api-Key": KEY}) as c:
            queue = iter(range(requests))

            async def worker():
                for _ in queue:
                    t = time.perf_counter()
                    r = await c.post("/v1/uploads", json=body)
                    latencies.append(time.perf_counter() - t)
                    assert r.status_code == 201, r.text

            start = time.perf_counter()
            await asyncio.gather(*(worker() for _ in range(concurrency)))
            elapsed = time.perf_counter() - start

    q = statistics.quantiles(latencies, n=100)
    return {"rps": requests / elapsed, "p50": q[49] * 1000, "p95": q[94] * 1000, "p99": q[98] * 1000}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requests", type=int, default=2_000)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args(argv)

    with mock_aws():
        if args.profile:
            import cProfile
            import pstats

            profiler = cProfile.Profile()
            result = profiler.runcall(asyncio.run, run(args.requests, args.concurrency))
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(15)
        else:
            result = asyncio.run(run(args.requests, args.concurrency))
    print(
        f"POST /v1/uploads x{args.requests}, concurrency {args.concurrency}: "
        f"{result['rps']:.0f} req/s  p50 {result['p50']:.1f} ms  p95 {result['p95']:.1f} ms  p99 {result['p99']:.1f} ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
