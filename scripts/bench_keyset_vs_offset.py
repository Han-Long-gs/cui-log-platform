"""Query benchmark on 1M log events: "latest 100 ERROR" latency, and keyset vs OFFSET pagination at page 1 / 100 / 1000.

    uv run python scripts/bench_keyset_vs_offset.py seed     --database-url <url>
    uv run python scripts/bench_keyset_vs_offset.py run      --database-url <url>
    uv run python scripts/bench_keyset_vs_offset.py cleanup  --database-url <url>

--database-url defaults to the DATABASE_URL environment variable; any driver in the URL is replaced by psycopg.

All rows belong to a dedicated bench tenant (BENCH_TENANT_ID), so other tenants' data is left alone and `cleanup`
removes exactly what `seed` added.

`run` times the real application query (query_api.service.fetch_log_events) for the keyset cases. The OFFSET cases
use the same SELECT, ORDER BY and LIMIT with OFFSET instead of a cursor condition. Each timed call opens a new
session, so the ORM identity map never serves rows from a previous iteration. The time measured is the wall time of
the Python call: query execution, network round trip to Postgres, and building the ORM objects.
"""

import argparse
import os
import platform
import random
import statistics
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from cui_db import LogEvent, Tenant, make_engine
from query_api.service import fetch_log_events
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session

BENCH_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000408")
PAGE_SIZE = 100
PAGES = (1, 100, 1000)
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results" / "keyset-vs-offset"

SERVICES = ("personal-site", "rag-agent", "music-api")
ENVIRONMENTS = (("production", 80), ("staging", 15), ("development", 5))
LEVELS = (("INFO", 70), ("DEBUG", 12), ("WARNING", 12), ("ERROR", 5), ("CRITICAL", 1))
COPY_COLUMNS = "event_id, tenant_id, service_name, environment, level, message, occurred_at, ingested_at"


# ---------- seed ----------


def seed(engine: Engine, rows: int, days: int, rng_seed: int) -> None:
    """Insert the bench tenant and `rows` log events for it with COPY, then VACUUM ANALYZE log_events.
    occurred_at is spread over the last `days` days and truncated to whole seconds, so many rows share a timestamp."""
    with Session(engine) as session, session.begin():
        if session.get(Tenant, BENCH_TENANT_ID) is not None:
            raise SystemExit("Bench tenant already exists: run `cleanup` first.")
        session.add(Tenant(id=BENCH_TENANT_ID, name="bench-4.8", retention_days=30, created_at=datetime.now(UTC)))

    rng = random.Random(rng_seed)
    env_names = [name for name, _ in ENVIRONMENTS]
    env_weights = [weight for _, weight in ENVIRONMENTS]
    level_names = [name for name, _ in LEVELS]
    level_weights = [weight for _, weight in LEVELS]
    now = datetime.now(UTC).replace(microsecond=0)
    span_seconds = days * 86400

    started = perf_counter()
    raw = engine.raw_connection()
    try:
        with raw.driver_connection.cursor() as cur:
            with cur.copy(f"COPY log_events ({COPY_COLUMNS}) FROM STDIN") as copy:
                for i in range(rows):
                    occurred_at = now - timedelta(seconds=rng.randrange(span_seconds))
                    service = rng.choice(SERVICES)
                    level = rng.choices(level_names, level_weights)[0]
                    copy.write_row(
                        (
                            uuid.UUID(int=rng.getrandbits(128), version=4),
                            BENCH_TENANT_ID,
                            service,
                            rng.choices(env_names, env_weights)[0],
                            level,
                            f"{service} {level.lower()} event #{i}",
                            occurred_at,
                            occurred_at + timedelta(milliseconds=rng.randrange(50, 2000)),
                        )
                    )
        raw.commit()
    finally:
        raw.close()
    print(f"Copied {rows:,} rows in {perf_counter() - started:.1f} s")

    vacuum_analyze(engine)
    print(f"Bench tenant: {BENCH_TENANT_ID}")
    for label, value in collect_environment(engine).items():
        print(f"  {label}: {value}")


def vacuum_analyze(engine: Engine) -> None:
    """VACUUM ANALYZE log_events: refresh planner statistics and the visibility map after a bulk load or delete.
    VACUUM cannot run inside a transaction block, hence the AUTOCOMMIT connection."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM ANALYZE log_events")


# ---------- run ----------


@dataclass
class Case:
    """One timed query: a name for the results table and a function that runs it once on a fresh session."""

    name: str
    run_once: Callable[[Session], Any]


def offset_page(session: Session, offset: int) -> list[LogEvent]:
    """Baseline: the bench tenant's events ordered like fetch_log_events, skipping `offset` rows with OFFSET.
    Fetches PAGE_SIZE + 1 rows, as fetch_log_events does to detect the next page."""
    stmt = (
        select(LogEvent)
        .where(LogEvent.tenant_id == BENCH_TENANT_ID)
        .order_by(LogEvent.occurred_at.desc(), LogEvent.id.desc())
        .offset(offset)
        .limit(PAGE_SIZE + 1)
    )
    return list(session.execute(stmt).scalars().all())


def keyset_page(session: Session, cursor: tuple[datetime, int] | None, level: str | None = None) -> Any:
    """The application query: one page of the bench tenant's events after `cursor`, optionally filtered by level."""
    return fetch_log_events(
        session,
        BENCH_TENANT_ID,
        service_name=None,
        environment=None,
        level=level,
        start=None,
        end=None,
        cursor=cursor,
        limit=PAGE_SIZE,
    )


def cursor_for_page(engine: Engine, page: int) -> tuple[datetime, int] | None:
    """The (occurred_at, id) of the last row of page - 1, i.e. the cursor a client holds when it asks for `page`.
    None for page 1. Located with OFFSET once, outside the timed loop."""
    if page == 1:
        return None
    stmt = (
        select(LogEvent.occurred_at, LogEvent.id)
        .where(LogEvent.tenant_id == BENCH_TENANT_ID)
        .order_by(LogEvent.occurred_at.desc(), LogEvent.id.desc())
        .offset((page - 1) * PAGE_SIZE - 1)
        .limit(1)
    )
    with Session(engine) as session:
        row = session.execute(stmt).one()
    return (row.occurred_at, row.id)


def build_cases(engine: Engine) -> list[Case]:
    """The latest-100-ERROR case, then keyset and OFFSET cases for every page in PAGES."""
    cases = [Case("latest 100 ERROR (keyset, page 1)", lambda s: keyset_page(s, None, level="ERROR"))]
    for page in PAGES:
        cursor = cursor_for_page(engine, page)
        cases.append(Case(f"keyset page {page}", lambda s, c=cursor: keyset_page(s, c)))
    for page in PAGES:
        offset = (page - 1) * PAGE_SIZE
        cases.append(Case(f"OFFSET page {page}", lambda s, o=offset: offset_page(s, o)))
    return cases


def time_case(engine: Engine, case: Case, warmup: int, iterations: int) -> list[float]:
    """Run the case `warmup` times untimed, then `iterations` times; return each timed run in milliseconds."""
    for _ in range(warmup):
        with Session(engine) as session:
            case.run_once(session)
    samples = []
    for _ in range(iterations):
        with Session(engine) as session:
            started = perf_counter()
            case.run_once(session)
            samples.append((perf_counter() - started) * 1000)
    return samples


def percentiles(samples: list[float]) -> dict[str, float]:
    """p50 / p95 / p99 of samples (inclusive method, so values stay within the observed range)."""
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return {"p50": cuts[49], "p95": cuts[94], "p99": cuts[98]}


def explain(engine: Engine, case: Case) -> str:
    """EXPLAIN (ANALYZE, BUFFERS) of the exact SQL and parameters the case sends to Postgres.
    The SQL is captured with a before_cursor_execute listener while the case runs once."""
    captured: list[tuple[str, Any]] = []

    def capture(_conn: Any, _cursor: Any, statement: str, parameters: Any, _context: Any, _executemany: bool) -> None:
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with Session(engine) as session:
            case.run_once(session)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    statement, parameters = captured[-1]
    with engine.connect() as conn:
        plan_rows = conn.exec_driver_sql(f"EXPLAIN (ANALYZE, BUFFERS) {statement}", parameters).all()
    plan = "\n".join(row[0] for row in plan_rows)
    return f"-- {case.name}\n-- parameters: {parameters}\n\n{statement}\n\n{plan}\n"


def collect_environment(engine: Engine) -> dict[str, str]:
    """Postgres version, row counts and on-disk sizes of log_events and each of its indexes."""
    with engine.connect() as conn:
        info = {
            "Postgres": conn.execute(text("SHOW server_version")).scalar_one(),
            "shared_buffers": conn.execute(text("SHOW shared_buffers")).scalar_one(),
            "Bench tenant rows": f"{conn.execute(text('SELECT count(*) FROM log_events WHERE tenant_id = :t'), {'t': BENCH_TENANT_ID}).scalar_one():,}",  # noqa: E501
            "All rows in log_events": f"{conn.execute(text('SELECT count(*) FROM log_events')).scalar_one():,}",
            "log_events table size": conn.execute(
                text("SELECT pg_size_pretty(pg_relation_size('log_events'))")
            ).scalar_one(),
        }
        index_rows = conn.execute(
            text(
                "SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) "
                "FROM pg_stat_user_indexes WHERE relname = 'log_events' ORDER BY indexrelname"
            )
        ).all()
    for name, size in index_rows:
        info[f"index {name}"] = size
    return info


def run(engine: Engine, warmup: int, iterations: int, out_dir: Path) -> None:
    """Time every case, write results.md and one EXPLAIN file per key query into out_dir."""
    with Session(engine) as session:
        if session.get(Tenant, BENCH_TENANT_ID) is None:
            raise SystemExit("Bench tenant not found: run `seed` first.")

    cases = build_cases(engine)
    results = []
    for case in cases:
        stats = percentiles(time_case(engine, case, warmup, iterations))
        results.append((case.name, stats))
        print(f"{case.name:<36} p50 {stats['p50']:8.2f} ms   p95 {stats['p95']:8.2f} ms   p99 {stats['p99']:8.2f} ms")

    explain_dir = out_dir / "explain"
    explain_dir.mkdir(parents=True, exist_ok=True)
    explain_targets = {
        "latest-100-error.txt": cases[0],
        f"keyset-page-{PAGES[-1]}.txt": next(c for c in cases if c.name == f"keyset page {PAGES[-1]}"),
        f"offset-page-{PAGES[-1]}.txt": next(c for c in cases if c.name == f"OFFSET page {PAGES[-1]}"),
    }
    for filename, case in explain_targets.items():
        (explain_dir / filename).write_text(explain(engine, case))

    write_results(engine, results, warmup, iterations, out_dir)
    print(f"Wrote {out_dir / 'results.md'} and {len(explain_targets)} plans in {explain_dir}")


def write_results(
    engine: Engine, results: list[tuple[str, dict[str, float]]], warmup: int, iterations: int, out_dir: Path
) -> None:
    """Write the latency table and the environment it was measured in to out_dir/results.md."""
    lines = [
        "# 4.8 query benchmark: raw results",
        "",
        "Generated by `scripts/bench_keyset_vs_offset.py run`. Do not edit by hand; rerun the script instead.",
        "",
        f"Page size {PAGE_SIZE}. Each case: {warmup} warm-up runs, then {iterations} timed runs on a fresh session.",
        "Time = wall time of the Python call (query + round trip + building ORM objects), in milliseconds.",
        "",
        "| Query | p50 (ms) | p95 (ms) | p99 (ms) |",
        "|---|---:|---:|---:|",
    ]
    for name, stats in results:
        lines.append(f"| {name} | {stats['p50']:.2f} | {stats['p95']:.2f} | {stats['p99']:.2f} |")
    lines += ["", "## Environment", "", f"- Measured at: {datetime.now(UTC).isoformat(timespec='seconds')}"]
    lines.append(f"- Client: {platform.platform()}, Python {platform.python_version()}")
    for label, value in collect_environment(engine).items():
        lines.append(f"- {label}: {value}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.md").write_text("\n".join(lines) + "\n")


# ---------- cleanup ----------


def cleanup(engine: Engine) -> None:
    """Delete the bench tenant and all its log events, then VACUUM ANALYZE log_events."""
    with Session(engine) as session, session.begin():
        deleted = session.execute(text("DELETE FROM log_events WHERE tenant_id = :t"), {"t": BENCH_TENANT_ID}).rowcount
        session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": BENCH_TENANT_ID})
    vacuum_analyze(engine)
    print(f"Deleted {deleted:,} bench rows and the bench tenant")


def main() -> None:
    """Parse the subcommand and its options, then run it."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"), help="defaults to $DATABASE_URL")
    sub = parser.add_subparsers(dest="command", required=True)
    seed_parser = sub.add_parser("seed", help="insert the bench tenant and its log events")
    seed_parser.add_argument("--rows", type=int, default=1_000_000)
    seed_parser.add_argument("--days", type=int, default=7)
    seed_parser.add_argument("--seed", type=int, default=408)
    run_parser = sub.add_parser("run", help="time the queries and write results")
    run_parser.add_argument("--warmup", type=int, default=5)
    run_parser.add_argument("--iterations", type=int, default=200)
    run_parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="defaults to results/keyset-vs-offset")
    sub.add_parser("cleanup", help="delete the bench tenant and its log events")
    args = parser.parse_args()

    if not args.database_url:
        parser.error("pass --database-url or set DATABASE_URL")
    engine = make_engine(args.database_url)
    try:
        if args.command == "seed":
            seed(engine, args.rows, args.days, args.seed)
        elif args.command == "run":
            run(engine, args.warmup, args.iterations, args.out)
        else:
            cleanup(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
