"""
-----------------
- sqlalchemy.py -
-----------------

SQLAlchemyDatabase: the base for SQL database plugins, usable with any SQLAlchemy URL
"""

# System Imports
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import cast

# Package Imports
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, Table
from sqlalchemy import inspect as sa_inspect
from sqlmodel import Session, SQLModel, create_engine, delete, select


class ColumnInfo(BaseModel):
    """One column of a table, as reflected from the database."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    nullable: bool


class TableInfo(BaseModel):
    """A table's name, primary key and columns, as reflected from the database."""

    model_config = ConfigDict(frozen=True)

    name: str
    primary_key: list[str]
    columns: list[ColumnInfo]


class SQLAlchemyDatabase:
    """One database connection. Each instance owns its own engine, so several can coexist."""

    def __init__(
        self,
        db_url: str,
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

    def list_tables(self) -> list[TableInfo]:
        """Describe every table in the database (its primary key and columns)."""
        insp = sa_inspect(self._engine)
        return [
            TableInfo(
                name=t,
                primary_key=insp.get_pk_constraint(t)["constrained_columns"],
                columns=[
                    ColumnInfo(name=c["name"], type=str(c["type"]), nullable=c["nullable"])
                    for c in insp.get_columns(t)
                ],
            )
            for t in insp.get_table_names()
        ]

    def delete(self, model: type[SQLModel], s: Session | None = None, **filters: object) -> int:
        """Delete rows matching filters. Returns count of deleted rows.

        Without `s` the delete is committed; in the caller's session the caller commits.
        """
        if s is None:
            with self.transaction() as s:
                return self._do_delete(model, s, **filters)
        return self._do_delete(model, s, **filters)

    def _do_delete(self, model: type[SQLModel], s: Session, **filters: object) -> int:
        stmt = delete(model)
        if filters:
            stmt = stmt.where(*[getattr(model, k) == v for k, v in filters.items()])
        return s.exec(stmt).rowcount

    def drop(self, model: type[SQLModel]) -> None:
        """Drop the table entirely. connect() creates it again."""
        # A SQLModel table model always maps a Table; inspect() only types it as a FromClause.
        table = cast(Table, sa_inspect(model).local_table)
        table.drop(self._engine)
