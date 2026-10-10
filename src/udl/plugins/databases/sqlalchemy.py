"""
-----------------
- sqlalchemy.py -
-----------------

SQLAlchemyDatabase: the base for SQL database plugins, usable with any SQLAlchemy URL
"""

# System Imports
from abc import abstractmethod
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import Final

# Package Imports
from sqlalchemy import Engine
from sqlalchemy import inspect as sa_inspect
from sqlmodel import Session, SQLModel, create_engine, select

DEFAULT_DB_PATH: Final[str] = "sqlite:///database.db"


class SQLAlchemyDatabase:
    """One database connection. Each instance owns its own engine, so several can coexist."""

    def __init__(
        self,
        db_url: str = DEFAULT_DB_PATH,
        *,
        echo: bool = False,
    ) -> None:
        self._engine: Engine = create_engine(
            url=db_url,
            echo=echo,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    def connect(self) -> None:
        """Open the database for use: creates the schema if it is missing."""
        self.migrate()

    def migrate(self) -> None:
        """Create any missing tables for the registered models. Safe to run repeatedly."""
        SQLModel.metadata.create_all(self._engine)

    def close(self) -> None:
        """Close every pooled connection."""
        self._engine.dispose()

    @contextmanager
    def session(self) -> Generator[Session]:
        # expire_on_commit=False keeps models usable after their session has closed.
        with Session(self._engine, expire_on_commit=False) as s:
            yield s

    @contextmanager
    def transaction(self) -> Generator[Session]:
        """A session that commits when the block succeeds and rolls back when it raises."""
        with self.session() as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    def add(
        self,
        models: SQLModel | Sequence[SQLModel],
        s: Session | None = None,
        commit_session: bool = True,
    ) -> None:
        """Insert model instances. Without `commit_session`, the caller's session must commit."""
        if s is None:
            with self.session() as s:
                self._do_add(
                    models,
                    s,
                    commit_session=commit_session,
                )
        else:
            self._do_add(
                models,
                s,
                commit_session=commit_session,
            )

    def _do_add(
        self,
        models: SQLModel | Sequence[SQLModel],
        s: Session,
        commit_session: bool = True,
    ) -> None:
        s.add_all([models] if isinstance(models, SQLModel) else models)
        self._do_commit(s, commit_session)

    def _do_commit(
        self,
        s: Session,
        commit_session: bool = True,
    ) -> None:
        if commit_session:
            s.commit()

    def list_rows[T: SQLModel](self, model: type[T]) -> list[T]:
        """Return every row of `model`'s table."""
        with self.session() as s:
            return list(s.exec(select(model)).all())

    def list_tables(self) -> None:
        """Print tables and their columns from the database."""
        insp = sa_inspect(self._engine)
        tables = insp.get_table_names()
        if not tables:
            print("No tables found.")
            return
        for t in tables:
            cols = insp.get_columns(t)
            pk = [c["name"] for c in cols if c.get("primary_key")]
            print(f"\n{t}  (pk: {', '.join(pk) or '—'})")
            for c in cols:
                nullable = "" if c["nullable"] else " NOT NULL"
                print(f"  {c['name']:>20}  {c['type']}{nullable}")

    def delete(self, model: type[SQLModel], s: Session | None = None, **filters) -> int:
        """Delete rows matching filters. Returns count of deleted rows."""
        if s is None:
            with self.session() as s:
                return self._do_delete(model, s, **filters)
        return self._do_delete(model, s, **filters)

    def _do_delete(self, model: type[SQLModel], s: Session, **filters) -> int:
        stmt = delete(model)
        if filters:
            stmt = stmt.where(*[getattr(model, k) == v for k, v in filters.items()])
        result = s.execute(stmt)
        if s is not None and not s.in_transaction():
            s.commit()
        return result.rowcount

    def drop(self, model: type[SQLModel]) -> None:
        """Drop the table entirely."""
        model.__table__.drop(self._engine)
