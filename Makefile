# Everything runs through `uv run` against the locked environment. UV_LOCKED makes every uv
# command fail if uv.lock is out of date with pyproject.toml. `uv run` syncs inexactly, so extras
# installed with `uv sync --all-extras` (e.g. playwright) are kept between runs.
export UV_LOCKED := 1

# pytest's workers (pytest-xdist, the user's call on 23 Nov 2026): four locally, where up to three
# suites run at once and each worker starts its own database containers; CI runs `make check
# PYTEST_WORKERS=0` (one process, as before) until its parallel runs are reliable: its first two
# had a test miss its timing and an aiosqlite thread outlive its loop, which no local run did.
PYTEST_WORKERS ?= 4

.PHONY: check check-py check-openapi check-web fmt test smoke web e2e openapi

check: check-py check-openapi check-web

check-py:
	uv lock --check
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest --cov -n $(PYTEST_WORKERS)

# The API's schema, and the web client's types and fixtures, which are made from it: committed,
# and regenerated with `make openapi` after any change to the API's routes.
check-openapi:
	uv run python -m udl.interfaces.api.openapi --check web/packages/api/openapi.json

openapi:
	uv run python -m udl.interfaces.api.openapi web/packages/api/openapi.json
	pnpm -C web --filter @udl/api run generate

# The web checks (Biome, TypeScript, Vitest with 90 % coverage, and the generated files, which
# the API's schema is).
check-web:
	pnpm -C web install --frozen-lockfile
	pnpm -C web check

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

# The web UI: the shell (and later the built-in UI plugins), into src/udl/…/web/dist.
web:
	pnpm -C web build

# The end-to-end tests, against a real `udl serve` (Playwright starts it).
e2e: web
	pnpm -C web e2e

# udl against real sites (tests/smoke): they need the internet, so they are never part of check.
smoke:
	uv run pytest -m network
