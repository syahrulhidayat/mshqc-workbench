#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(round((pct / 100.0) * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


async def _run_phase(
    base_url: str,
    total_requests: int,
    concurrency: int,
    *,
    unique_payloads: bool,
) -> dict[str, float | int]:
    sem = asyncio.Semaphore(concurrency)
    latencies_ms: list[float] = []
    cache_hits = 0
    errors = 0

    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
        async def send_one(idx: int) -> None:
            nonlocal cache_hits, errors
            offset = idx * 0.001 if unique_payloads else 0.0
            payload = {
                "geometry_xyz": (
                    f"O 0 0 0\nH {0.757 + offset:.6f} 0.586 0\nH -0.757 0.586 0"
                ),
                "method": "RHF",
                "basis": "cc-pvdz",
                "charge": 0,
                "multiplicity": 1,
            }
            async with sem:
                t0 = time.perf_counter()
                try:
                    resp = await client.post("/api/v1/jobs", json=payload)
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    if resp.status_code in (200, 201):
                        latencies_ms.append(elapsed)
                        if resp.json().get("cache_hit"):
                            cache_hits += 1
                    else:
                        errors += 1
                except httpx.HTTPError:
                    errors += 1

        t_start = time.perf_counter()
        await asyncio.gather(*(send_one(i) for i in range(total_requests)))
        total_s = time.perf_counter() - t_start

    latencies_ms.sort()
    return {
        "requests": total_requests,
        "errors": errors,
        "cache_hits": cache_hits,
        "throughput_rps": round(len(latencies_ms) / max(total_s, 1e-6), 2),
        "p50_ms": round(statistics.median(latencies_ms), 2) if latencies_ms else 0.0,
        "p95_ms": round(_percentile(latencies_ms, 95), 2),
        "p99_ms": round(_percentile(latencies_ms, 99), 2),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark mshqc-workbench API throughput and dedup cache."
    )
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    miss_stats = await _run_phase(args.url, args.requests, args.concurrency, unique_payloads=True)
    hit_stats = await _run_phase(args.url, args.requests, args.concurrency, unique_payloads=False)

    print("Phase 1 - Unique Payloads (Cache Miss + Enqueue):", miss_stats)
    print("Phase 2 - Identical Payloads (SHA-256 Cache Hit):", hit_stats)


if __name__ == "__main__":
    asyncio.run(main())
