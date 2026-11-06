# run from root
psql -h localhost -d cui_bench -c "TRUNCATE TABLE log_events, tenants RESTART IDENTITY CASCADE"
psql -h localhost -d cui_bench -c "select count(*) from log_events"
uv run --env-file .env.bench python -m scripts.bench_insert_row_vs_batch
psql -h localhost -d cui_bench -c "select count(*) from log_events"