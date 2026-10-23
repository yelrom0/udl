# Everything runs through `uv run` against the locked environment. UV_LOCKED makes every uv
# command fail if uv.lock is out of date with pyproject.toml. `uv run` syncs inexactly, so extras
# installed with `uv sync --all-extras` (e.g. playwright) are kept between runs.
export UV_LOCKED := 1

.PHONY: check fmt test smoke

check:
	uv lock --check
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest --cov

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

# udl against real sites (tests/smoke): they need the internet, so they are never part of check.
smoke:
	uv run pytest -m network
