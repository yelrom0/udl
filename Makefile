# Everything runs through `uv run` against the locked environment. UV_LOCKED makes every uv
# command fail if uv.lock is out of date with pyproject.toml. `uv run` syncs inexactly, so extras
# installed with `uv sync --all-extras` (e.g. playwright) are kept between runs.
export UV_LOCKED := 1

.PHONY: check check-py check-web fmt test smoke web e2e

check: check-py check-web

check-py:
	uv lock --check
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest --cov

# The web checks (Biome, TypeScript, Vitest with 90 % coverage). The openapi check comes with
# the typed client (18.2).
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
