"""Seed a running stack with realistic demo logs through the public entry point (nginx -> ingestion-api).

    uv run python scripts/seed_demo.py                      # 50 batches x 200 events = 10,000 events
    uv run python scripts/seed_demo.py --batches 5 --seed 7

The seed fixes services, levels, messages and timestamps. Event ids are always new (uuid4), so every run adds rows
instead of being dropped as duplicates. Timestamps fall within the last --hours hours, relative to the run time.
"""

import argparse
import random
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2

ENVIRONMENTS = {"production": 70, "staging": 20, "development": 10}
LEVELS = {"DEBUG": 15, "INFO": 60, "WARNING": 15, "ERROR": 8, "CRITICAL": 2}
SERVICES = {"personal-site": 30, "rag-agent": 40, "music-api": 30}

# message templates per service and level; {n} is filled with a random number
MESSAGES: dict[str, dict[str, list[str]]] = {
    "personal-site": {
        "DEBUG": ["cache lookup for /blog/{n}", "rendered template post.html in {n} ms"],
        "INFO": ["GET /blog/{n} 200", "GET / 200", "GET /projects 200", "contact form submitted"],
        "WARNING": ["GET /blog/{n} 404", "slow page render: {n} ms", "image larger than 2 MB served uncompressed"],
        "ERROR": ["contact form mail delivery failed: SMTP timeout", "GET /rss.xml 500: template variable missing"],
        "CRITICAL": ["TLS certificate expires in {n} hours"],
    },
    "rag-agent": {
        "DEBUG": ["retrieved {n} chunks for query", "prompt token count {n}", "embedding cache hit"],
        "INFO": ["answered question in {n} ms", "indexed document doc-{n}", "new chat session started"],
        "WARNING": [
            "retrieval returned 0 chunks, falling back to web search",
            "LLM call retried after rate limit",
            "context truncated to fit {n} tokens",
        ],
        "ERROR": [
            "LLM call failed after 3 retries: upstream 503",
            "vector store query timed out after {n} ms",
            "failed to parse tool call arguments",
        ],
        "CRITICAL": ["vector store unreachable: connection refused"],
    },
    "music-api": {
        "DEBUG": ["SQL query took {n} ms", "auth token validated for user {n}"],
        "INFO": ["GET /tracks/{n} 200", "POST /playlists 201", "GET /search?q=jazz 200", "user {n} logged in"],
        "WARNING": ["GET /tracks/{n} 404", "rate limit at 80% for client {n}", "slow query: {n} ms"],
        "ERROR": ["POST /playlists 500: unique constraint violated", "payment webhook signature invalid"],
        "CRITICAL": ["database connection pool exhausted"],
    },
}


def pick(rng: random.Random, weights: dict[str, int]) -> str:
    """Return one key of weights, chosen with probability proportional to its value."""
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def make_custom(rng: random.Random, service: str, level: str) -> dict[str, Any]:
    """Return service-specific structured fields for the custom column."""
    if service == "rag-agent":
        return {"model": rng.choice(["small", "large"]), "latency_ms": rng.randint(200, 9000)}
    status = 500 if level in ("ERROR", "CRITICAL") else rng.choice([200, 200, 200, 201, 404])
    return {"status_code": status, "duration_ms": rng.randint(2, 1500)}


def make_event(rng: random.Random, now: datetime, hours: float) -> dict[str, Any]:
    """Return one valid log event as a JSON-ready dict, with a new event_id."""
    service = pick(rng, SERVICES)
    level = pick(rng, LEVELS)
    occurred_at = now - timedelta(seconds=rng.uniform(0, hours * 3600))
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "service_name": service,
        "environment": pick(rng, ENVIRONMENTS),
        "level": level,
        "message": rng.choice(MESSAGES[service][level]).format(n=rng.randint(1, 999)),
        "occurred_at": occurred_at.isoformat(),
    }
    if rng.random() < 0.3:
        event["trace_id"] = str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if rng.random() < 0.5:
        event["custom"] = make_custom(rng, service, level)
    return event


def seed(url: str, batches: int, batch_size: int, seed_value: int, hours: float) -> bool:
    """POST batches of generated events to url/v1/logs and print the accepted distribution; True if all got 202."""
    rng = random.Random(seed_value)
    now = datetime.now(UTC)
    statuses: Counter[int | str] = Counter()
    by_service: Counter[str] = Counter()
    by_level: Counter[str] = Counter()
    with httpx2.Client(base_url=url, timeout=10) as client:
        for _ in range(batches):
            events = [make_event(rng, now, hours) for _ in range(batch_size)]
            try:
                status: int | str = client.post("/v1/logs", json={"log_entries": events}).status_code
            except httpx2.HTTPError as exc:
                status = type(exc).__name__
            statuses[status] += 1
            if status == 202:
                by_service.update(e["service_name"] for e in events)
                by_level.update(e["level"] for e in events)
    print(f"batches: {dict(statuses)}")
    print(f"accepted events: {by_service.total()}")
    print(f"by service: {dict(sorted(by_service.items()))}")
    print(f"by level: {dict(sorted(by_level.items()))}")
    return statuses[202] == batches


def main() -> None:
    """Parse arguments, seed the stack and exit non-zero unless every batch was accepted."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1", help="nginx address")
    parser.add_argument("--batches", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--hours", type=float, default=24, help="spread occurred_at over this many past hours")
    args = parser.parse_args()
    ok = seed(args.url, args.batches, args.batch_size, args.seed, args.hours)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
