"""Worker-crash fault test: send a steady stream of batches to the ingestion API, then check every accepted event
against the database.

    uv run --env-file .env.dev python loadtests/fault/worker_crash.py send --out run1.jsonl --duration 60
    uv run --env-file .env.dev python loadtests/fault/worker_crash.py verify --file run1.jsonl

`send` writes one JSON line per batch the API accepted (202): its batch_id and event_ids. `verify` reads that file
and reports, for those ids only, events missing from log_events, events stored more than once, and the batches'
ingest_batches status and retry_count.
"""

import argparse
import json
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from cui_config import settings
from cui_db import make_engine
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID


def make_events(count: int) -> list[dict[str, str]]:
    """count valid log events, each with a new event_id."""
    now = datetime.now(UTC).isoformat()
    return [
        {
            "event_id": str(uuid.uuid4()),
            "service_name": "fault-test",
            "environment": "dev",
            "level": "INFO",
            "message": "worker crash test",
            "occurred_at": now,
        }
        for _ in range(count)
    ]


def send(url: str, out: Path, batch_size: int, rate: float, duration: float) -> None:
    """POST batches of batch_size events at about rate batches per second for duration seconds; append each
    accepted batch to out. Prints progress every 10 batches and a summary of status codes at the end."""
    interval = 1 / rate
    statuses: Counter[int | str] = Counter()
    deadline = time.monotonic() + duration
    with httpx2.Client(base_url=url, timeout=5) as client, out.open("a") as file:
        while time.monotonic() < deadline:
            started = time.monotonic()
            events = make_events(batch_size)
            try:
                response = client.post("/v1/logs", json={"log_entries": events})
                statuses[response.status_code] += 1
                if response.status_code == 202:
                    record = {"batch_id": response.json()["batch_id"], "event_ids": [e["event_id"] for e in events]}
                    file.write(json.dumps(record) + "\n")
                    file.flush()
            except httpx2.HTTPError as error:
                statuses[type(error).__name__] += 1
            sent = sum(statuses.values())
            if sent % 10 == 0:
                print(f"{time.strftime('%H:%M:%S')} sent {sent} batches: {dict(statuses)}", flush=True)
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
    print(f"done: {dict(statuses)}; accepted batches appended to {out}")


def verify(file: Path) -> None:
    """Check every batch and event recorded in file against the database configured by DATABASE_URL."""
    records = [json.loads(line) for line in file.read_text().splitlines() if line]
    batch_ids = [record["batch_id"] for record in records]
    event_ids = [event_id for record in records for event_id in record["event_ids"]]

    engine = make_engine(settings.database_url)
    ids = bindparam("ids", type_=ARRAY(UUID))
    with engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT event_id::text, count(*) FROM log_events WHERE event_id = ANY(:ids) GROUP BY event_id"
            ).bindparams(ids),
            {"ids": event_ids},
        ).all()
        batches = conn.execute(
            text("SELECT id::text, status, retry_count FROM ingest_batches WHERE id = ANY(:ids)").bindparams(ids),
            {"ids": batch_ids},
        ).all()
    engine.dispose()

    rows_per_event = {event_id: count for event_id, count in stored}
    missing = [event_id for event_id in event_ids if event_id not in rows_per_event]
    duplicated = {event_id: count for event_id, count in rows_per_event.items() if count > 1}
    status_of = {batch_id: status for batch_id, status, _ in batches}

    print(f"accepted batches: {len(batch_ids)}, accepted events: {len(event_ids)}")
    print(f"events in log_events: {len(rows_per_event)}")
    print(f"missing events: {len(missing)}")
    print(f"events stored more than once: {len(duplicated)}")
    print(f"batch status: {dict(Counter(status_of.get(batch_id, 'NO ROW') for batch_id in batch_ids))}")
    print(f"retry_count: {dict(sorted(Counter(retry_count for _, _, retry_count in batches).items()))}")
    if missing:
        missing_set = set(missing)
        lost_batches = sorted({r["batch_id"] for r in records if any(e in missing_set for e in r["event_ids"])})
        print(f"batches with missing events: {lost_batches[:10]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    send_parser = commands.add_parser("send", help="send batches and record the accepted ones")
    send_parser.add_argument("--url", default="http://127.0.0.1:8000")
    send_parser.add_argument("--out", type=Path, required=True, help="JSON-lines file accepted batches go to")
    send_parser.add_argument("--batch-size", type=int, default=200)
    send_parser.add_argument("--rate", type=float, default=5, help="batches per second")
    send_parser.add_argument("--duration", type=float, default=60, help="seconds")
    verify_parser = commands.add_parser("verify", help="check recorded batches against the database")
    verify_parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "send":
        send(args.url, args.out, args.batch_size, args.rate, args.duration)
    else:
        verify(args.file)


if __name__ == "__main__":
    main()
