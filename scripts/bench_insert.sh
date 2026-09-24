# run from root
psql -h localhost -d cui_dev -c "TRUNCATE TABLE log_events, tenants RESTART IDENTITY CASCADE"
psql -h localhost -d cui_dev -c "select count(*) from log_events"
uv run python -m scripts.bench_insert
psql -h localhost -d cui_dev -c "select count(*) from log_events"