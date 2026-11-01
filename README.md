# Cui

a multi-tenant log ingestion platform

## Quickstart

### Prerequisites

- macOS: Docker Desktop + uv
- Windows: WSL2 Ubuntu -> enable WSL Integration -> install uv in Ubuntu
  - run every command below in the Ubuntu shell
- idle localhost:80

### Run

```bash
git clone https://github.com/Han-Long-gs/cui-log-platform.git
cd cui-log-platform
cp .env.example .env    # replace every <placeholder>, letters and digits only
docker compose up -d --build
```

The first run takes 1-2 minutes (pulls images and builds). Then check:

```bash
docker compose ps
```

`migrate` should be `Exited (0)`; every other service `running` or `healthy`.

### Load demo data

```bash
uv run python scripts/seed_demo.py
```

Sends 10,000 logs (3 services, 3 environments, 5 levels, last 24 hours) through the same entry point a client would use.
Open http://localhost and filter by service / environment / level.

### Stop

```bash
docker compose down       # keeps the data
docker compose down -v    # also deletes the database and queue data
```
