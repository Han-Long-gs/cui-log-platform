# Architecture

## System overview

```mermaid
flowchart LR
    producer["Producer app<br/>(POST batches of logs)"]
    browser["Browser"]

    subgraph compose["Docker Compose network"]
        nginx["nginx<br/>only port published to the host<br/>body limit 1 MiB"]
        frontend[("Frontend static files<br/>React build, served by nginx")]
        ingest["ingestion-api<br/>FastAPI"]
        query["query-api<br/>FastAPI"]
        mq[["RabbitMQ<br/>queue: celery"]]
        worker["worker<br/>Celery, prefork"]
        pg[("PostgreSQL<br/>log_events, ingest_batches")]
        migrate["migrate<br/>Alembic, runs once at startup"]
    end

    producer -->|"POST /v1/logs"| nginx
    browser -->|"GET /"| nginx
    browser -->|"GET /api/v1/logs, /api/v1/stats"| nginx

    nginx -->|"/"| frontend
    nginx -->|"/v1/logs"| ingest
    nginx -->|"/api/"| query

    ingest -->|"queue mode: publish batch"| mq
    mq -->|"consume, ack after commit"| worker
    worker -->|"batch INSERT"| pg
    ingest -.->|"sync mode baseline: INSERT"| pg
    query -->|"keyset-paginated SELECT"| pg
    migrate -.->|"alembic upgrade head"| pg
```

The ingestion API has two modes, chosen by `INGEST_MODE`. Queue mode is the default. Sync mode is the baseline kept for benchmarking.

## Sync pipeline (baseline, `INGEST_MODE=sync`)

```mermaid
sequenceDiagram
    autonumber
    participant C as Producer
    participant N as nginx
    participant A as ingestion-api
    participant P as PostgreSQL

    C->>N: POST /v1/logs (1-200 events)
    alt body over 1 MiB
        N-->>C: 413, request never reaches the API
    end
    N->>A: forward request
    A->>A: validate with Pydantic<br/>normalize level and timestamps
    alt invalid event or batch size
        A-->>C: 422, nothing written
    end
    A->>P: BEGIN, INSERT all events, COMMIT
    alt database error (down, duplicate event_id)
        P-->>A: error, whole batch rolled back
        A-->>C: 500
    else committed
        P-->>A: ok
        A-->>C: 201 Created with batch_id
    end
    Note over C,P: The client waits for the database on every request.<br/>If PostgreSQL is slow or down, ingestion is slow or down.
```

## Async pipeline (`INGEST_MODE=queue`, default)

### Request path: accept and publish

```mermaid
sequenceDiagram
    autonumber
    participant C as Producer
    participant N as nginx
    participant A as ingestion-api
    participant Q as RabbitMQ

    C->>N: POST /v1/logs (1-200 events)
    alt body over 1 MiB
        N-->>C: 413, request never reaches the API
    end
    N->>A: forward request
    A->>A: validate with Pydantic<br/>assign batch_id, tenant_id, received_at
    alt invalid event or batch size
        A-->>C: 422, nothing published
    end
    A->>Q: publish one message per batch (publisher confirms)
    alt broker unreachable or no confirm in time
        A-->>C: 503, client should retry
    else broker confirmed
        Q-->>A: confirm
        A-->>C: 202 Accepted with batch_id
    end
    Note over C,Q: The API never touches PostgreSQL.<br/>It keeps returning 202 while the database or the worker is down.
```

### Worker path: consume and persist

```mermaid
sequenceDiagram
    autonumber
    participant Q as RabbitMQ
    participant M as worker main process
    participant W as worker child process
    participant P as PostgreSQL

    Q->>M: deliver message (prefetch 1, not acked yet)
    M->>W: hand the task to a child
    W->>W: validate the batch message
    alt invalid message
        W->>P: record the batch as FAILED (if its ids are readable)
        W-->>M: raise, no retry
        M->>Q: ack (message removed)
    end
    W->>P: transaction 1: upsert ingest_batches row as PROCESSING
    alt persisted_at already set (redelivered after commit)
        P-->>W: already persisted
        W-->>M: return early, skip the INSERT
        M->>Q: ack
    end
    W->>P: transaction 2: INSERT log_events ON CONFLICT DO NOTHING<br/>and set the batch to PERSISTED, then COMMIT
    alt transient error (connection lost, timeout)
        W->>Q: retry: publish a new message with exponential backoff (up to 10 times)
        M->>Q: ack the original message
    else permanent error, or retries used up
        W->>P: mark the batch FAILED
        M->>Q: ack
    else child killed mid-task
        M->>M: WorkerLostError
        M->>Q: reject and requeue (same message delivered again)
    else committed
        W-->>M: task succeeded
        M->>Q: ack (only now is the message removed)
    end
```

Acks are sent only after the task finishes (`task_acks_late`), so a crash before that point leaves the message in RabbitMQ to be delivered again. Two guards keep a redelivery from storing a batch twice: the `persisted_at` check above, and `UNIQUE (tenant_id, event_id)` with `ON CONFLICT DO NOTHING`.
