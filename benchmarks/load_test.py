#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import platform
import re
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

BENCH_START = "<!-- BENCHMARK_START -->"
BENCH_END = "<!-- BENCHMARK_END -->"


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, round((pct / 100.0) * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def _detect_cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("model name"):
                _, _, value = line.partition(":")
                if value.strip():
                    return value.strip()
    return platform.processor() or platform.machine()


def _hardware_summary() -> str:
    cpu = _detect_cpu_model()
    threads = os.cpu_count() or 1
    os_info = f"{platform.system()} {platform.release()}"
    py_ver = platform.python_version()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d UTC")
    return f"{cpu} ({threads} threads), {os_info}, Python {py_ver}, measured on {stamp}"


async def _run_phase(
    base_url: str,
    total_requests: int,
    concurrency: int,
    *,
    unique_payloads: bool,
    run_salt: float,
) -> dict[str, float | int]:
    sem = asyncio.Semaphore(concurrency)
    latencies_ms: list[float] = []
    cache_hits = 0
    errors = 0

    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
        if not unique_payloads:
            # Warm up one canonical entry so Phase 2 measures steady-state cache hits.
            warmup = {
                "geometry_xyz": (
                    f"O 0 0 0\nH {0.757 + run_salt:.6f} 0.586 0\nH -0.757 0.586 0"
                ),
                "method": "RHF",
                "basis": "cc-pvdz",
                "charge": 0,
                "multiplicity": 1,
            }
            await client.post("/api/v1/jobs", json=warmup)

        async def send_one(idx: int) -> None:
            nonlocal cache_hits, errors
            offset = (run_salt + (idx + 1) * 0.0005) if unique_payloads else run_salt
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
        "concurrency": concurrency,
        "errors": errors,
        "cache_hits": cache_hits,
        "throughput_rps": round(len(latencies_ms) / max(total_s, 1e-6), 2),
        "p50_ms": round(statistics.median(latencies_ms), 2) if latencies_ms else 0.0,
        "p95_ms": round(_percentile(latencies_ms, 95), 2),
        "p99_ms": round(_percentile(latencies_ms, 99), 2),
    }


def _format_markdown_block(
    miss: dict[str, float | int],
    hit: dict[str, float | int],
    hw: str,
) -> str:
    header = (
        "| Workload Scenario | Requests | Concurrency | Cache Hits | Errors "
        "| Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) |\n"
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n"
    )
    row_miss = (
        f"| Unique Jobs (Cache Miss + Enqueue) | {miss['requests']} | {miss['concurrency']} "
        f"| {miss['cache_hits']} | {miss['errors']} | {miss['throughput_rps']} "
        f"| {miss['p50_ms']} | {miss['p95_ms']} | {miss['p99_ms']} |\n"
    )
    row_hit = (
        f"| Identical Canonical Geometry (SHA-256 Hit) | {hit['requests']} | {hit['concurrency']} "
        f"| {hit['cache_hits']} | {hit['errors']} | {hit['throughput_rps']} "
        f"| {hit['p50_ms']} | {hit['p95_ms']} | {hit['p99_ms']} |\n"
    )
    return f"{BENCH_START}\n{header}{row_miss}{row_hit}\n*Hardware baseline: {hw}.*\n{BENCH_END}"


def _update_readme(readme_path: Path, block: str) -> None:
    content = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(BENCH_START) + r".*?" + re.escape(BENCH_END),
        flags=re.DOTALL,
    )
    if not pattern.search(content):
        raise SystemExit(
            f"Markers '{BENCH_START}' / '{BENCH_END}' not found in {readme_path}."
        )
    updated = pattern.sub(block, content, count=1)
    readme_path.write_text(updated, encoding="utf-8")
    print(f"[UPDATED] Benchmark table written to {readme_path}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark mshqc-workbench API throughput and dedup cache."
    )
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument(
        "--update-readme",
        type=Path,
        default=None,
        help="Optional path to README.md to populate measured benchmark results.",
    )
    args = parser.parse_args()

    # Deterministic per-run geometric offset so repeated runs test real misses cleanly.
    run_salt = round((time.time() % 1000) * 1e-4, 6)

    miss_stats = await _run_phase(
        args.url,
        args.requests,
        args.concurrency,
        unique_payloads=True,
        run_salt=run_salt,
    )
    hit_stats = await _run_phase(
        args.url,
        args.requests,
        args.concurrency,
        unique_payloads=False,
        run_salt=run_salt,
    )
    hw = _hardware_summary()

    print("Hardware:", hw)
    print("Phase 1 - Unique Payloads (Cache Miss + Enqueue):", miss_stats)
    print("Phase 2 - Identical Payloads (SHA-256 Cache Hit):", hit_stats)

    if args.update_readme is not None:
        block = _format_markdown_block(miss_stats, hit_stats, hw)
        _update_readme(args.update_readme, block)


if __name__ == "__main__":
    asyncio.run(main())
