uv run --env-file .env.test pytest --cov
uv run ruff check . --fix
uv run ruff format --check .