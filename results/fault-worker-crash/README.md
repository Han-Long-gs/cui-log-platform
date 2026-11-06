# Fault Test: `kill -9` the Worker Under Steady Traffic

Question: if the Celery worker is killed while batches keep arriving, does any batch that got a `202` go missing, or get stored twice?

Short answer, for the three runs below: **no missing events and no duplicates**. That includes three kills that landed while a child process was running a batch. The scope is narrow, so read [What this does not cover](#what-this-does-not-cover) before quoting it.

## Setup

- Tool: [`scripts/fault_worker_crash.py`](../../scripts/fault_worker_crash.py).
  - `send` POSTs batches of 200 events to `POST /v1/logs` and records every batch that got a `202` (its `batch_id` and all `event_id`s).
  - `verify` checks exactly those ids against Postgres: events missing from `log_events`, events stored more than once, and each batch's `ingest_batches` status and `retry_count`.
- Processes (2026-10-18, macOS, run locally, not in Docker Compose): one ingestion API (uvicorn), one Celery worker with 2 child processes (`-c 2`), Postgres on the host, RabbitMQ in a container.
- Worker settings that matter here:
  - `task_acks_late=True`: the message is acked only after the task returns, so after the database commit
  - `task_reject_on_worker_lost=True`: if a child dies mid-task, the message is requeued instead of acked
  - `worker_prefetch_multiplier=1`
- Deduplication: `UNIQUE (tenant_id, event_id)` plus `ON CONFLICT DO NOTHING`, one database transaction per batch.

## Results

| Run | What was killed | Batches accepted (`202`) | Events | Missing | Duplicated | Batch status |
|---|---|---:|---:|---:|---:|---|
| 1. Control | nothing | 147 | 29,400 | 0 | 0 | all `PERSISTED`, `retry_count` 0 |
| 2. Child processes | one child every second, 40 times | 588 | 117,600 | 0 | 0 | all `PERSISTED`, `retry_count` 0 |
| 3. Whole worker | main process and its children at once, restarted ~2 min later | 294 | 58,800 | 0 | 0 | all `PERSISTED`, `retry_count` 0 |

Every batch in every run got a `202`.

### Run 1: control

30 s at about 5 batches/s. The worker log shows each of the 147 tasks received and run exactly once, so there were no redeliveries without a fault.

### Run 2: kill child processes

60 s of traffic (about 9.8 batches/s; `send` is single-threaded and that was its ceiling). One child process was killed every second. The main process replaced each killed child and the worker never went down.

Most kills landed between two tasks: a batch takes about 30 ms, so a child sits idle most of the time. The worker log shows **3 kills that hit a running task** (`WorkerLostError`, jobs 343, 392 and 493). Job 493, step by step:

| Time | Worker log | Meaning |
|---|---|---|
| 49.518 | `Task ingest_batch[2675e3f0…] received` | first delivery |
| 49.519 | `SpawnPoolWorker-8` starts the task | |
| 49.563 | `SpawnPoolWorker-8 … exited with 'signal 9 (SIGKILL)'` | killed 44 ms in; a normal batch finishes in about 30 ms |
| 49.575 | `WorkerLostError(… Job: 493.)` | the main process notices; the message is rejected and requeued |
| 49.617 | `Task ingest_batch[2675e3f0…] received` | **the same task id** delivered again, 54 ms after the kill |
| 49.791 | `SpawnPoolWorker-9` starts the task | a replacement child picks it up |
| 49.848 | `succeeded` | finished |

`verify` shows this batch as `PERSISTED`, with exactly 200 rows and `retry_count` 0. The redelivery comes from RabbitMQ requeueing the message, not from a Celery retry, so the retry counter does not move.

### Run 3: kill the whole worker

About 15 s into a 60 s run, the main process and both children were killed together. The API kept answering `202`, and the queue grew while no worker existed (ready / unacked):

```
8 / 0 → 51 / 0 → 83 / 0 → 159 / 0 → 240 / 0 → peak 288 / 0
```

Unacked stayed at 0: when the worker's connection to RabbitMQ dropped, RabbitMQ put every unacked message on that connection back in the queue. After the restart, the worker drained all 288 batches in 2.8 s (about 100 batches/s, or roughly 20,000 events/s with 2 children). Each one was received and succeeded exactly once. The 294 accepted batches are the 6 the old worker finished before the kill plus these 288.

### Targeted check: crash after commit, before ack

The three runs never hit the gap between the database commit and the ack. It lasts well under a millisecond, and none of the worker logs show a redelivered batch being skipped. This case was tested separately (2026-10-13), with one batch and no background traffic:

1. A temporary `time.sleep(30)` was added right after the transaction commits, before the task returns, so the ack has not been sent yet.
2. One batch of 3 events was sent. During the sleep, the database already showed it as `PERSISTED` with 3 rows.
3. The worker's main process and children were killed with `kill -9`, then the worker was restarted.
4. RabbitMQ redelivered the message. The worker logged `ingest batch 15fda9a9… is already persisted. skipping...` and the task succeeded in 0.023 s, without sleeping again.
5. `started_at` and `persisted_at` were identical to the values before the crash, and the batch still had exactly 3 rows.

What prevents the duplicate here is the batch state, not the unique constraint. The events and the `PERSISTED` status are committed in the same transaction. On redelivery, the worker checks `persisted_at` before doing any work, and returns early if it is set. `UNIQUE (tenant_id, event_id)` with `ON CONFLICT DO NOTHING` is the second line of defense, for cases such as a client resending the same events under a new batch.

## What this does not cover

- **Run 3 did not kill a task mid-run.** The old worker's last task had already succeeded when the kill landed. Run 3 shows that queued messages survive the whole worker disappearing. The mid-task case is covered only by the 3 kills in run 2.
- **"Mid-task" means the child was running the task, not necessarily inside the INSERT.** `WorkerLostError` shows a task was in progress. For job 493 the kill landed 44 ms in, against about 30 ms for a normal batch, so it was most likely writing to the database. The log does not show which statement was running.
- **Small sample.** 3 mid-task kills, 2 child processes, one tenant, one machine.
- **Crash after commit was tested once, without traffic.** The targeted check above used a single batch and a temporary sleep in the task.
- **Not in Docker, and not Linux.** On macOS, Celery starts children with `spawn`; on Linux the default is `fork`. These runs were done before the system moved to Docker Compose.
- **Only the worker was faulted.** RabbitMQ going down, Postgres going down under load, and a slow database were not part of this test.
- **The run 2 worker log is incomplete.** Its first half was truncated while it was being written, so the log covers 297 of the 588 batches (14 of the 40 kills). `verify` still checked all 588 against the database.

## Known ways to lose data (not exercised here)

These follow from the current design and are accepted for V1. A dead-letter exchange is planned for later.

- **Postgres down longer than the retry window.** Transient database errors are retried up to 10 times with backoff (about 40–75 s in total). After that the batch is dropped. It cannot be marked `FAILED` either, since the database is still down, so the only trace is the worker log.
- **Broker unreachable at the moment of a retry.** A Celery retry publishes a new message. If that publish fails, the task is rejected without requeue and the batch is lost.
- **The client gives up on a `503`.** If the API cannot publish to RabbitMQ, it returns `503` instead of `202`. Nothing is lost on the server side, but the caller has to retry. No SDK does that yet.

## Open question

In run 2, one batch (job 343) was received but did not start for 5 s, although a child was idle. When a child did start it, it stalled for another 5 s without finishing, until that child was killed. A replacement child then completed it in 47 ms. Other children were processing batches in about 30 ms at the same time. It was persisted once with no duplicates, but the cause of the two stalls is unknown. Next time, check `pg_stat_activity` while it is happening.

## Reproduce

```bash
uv run --env-file .env.dev python scripts/fault_worker_crash.py send --out run1.jsonl --duration 60
uv run --env-file .env.dev python scripts/fault_worker_crash.py verify --file run1.jsonl
```

Start `verify` only after the queue is empty. Raw files (`run*.jsonl`, `worker-run*.log`) are kept locally under `raw/` and are not committed.

## Authorship

Han ran the tests. Claude drafted this write-up from the raw logs and `verify` output.
