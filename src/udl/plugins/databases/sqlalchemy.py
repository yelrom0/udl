"""
-----------------
- sqlalchemy.py -
-----------------

SQLAlchemyDatabase: the base for SQL database plugins, usable with any SQLAlchemy URL
"""

# System Imports
from abc import abstractmethod
from contextlib import contextmanager
from typing import Final, Generator, Optional

# Package Imports
from sqlalchemy import inspect as sa_inspect
from sqlmodel import create_engine, Session, SQLModel

DEFAULT_DB_PATH: Final[str] = "sqlite:///database.db"


class SQLAlchemyDatabase(object):
    _instances = {}

    def __new__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__new__(cls)
        return cls._instances[cls]

    def __init__(
        self,
        db_url: Optional[str] = DEFAULT_DB_PATH,
        echo: Optional[bool] = False,
    ) -> None:
        if not hasattr(
            self,
            "_initialized",
        ):
            self._engine: Engine = create_engine(
                url=db_url,
                echo=echo,
            )
            self._initialized = True

    @property
    def engine(self):
        return self._engine

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        with Session(self._engine) as s:
            yield s

    @contextmanager
    def transacrion(self) -> Generator[Session, Any, None]:
        with Session(self._engine) as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    def add(
        self,
        models: type[SQLModel] | list[type[SQLModel]],
        s: Session | None = None,
        commit_session: Optional[bool] = True,
    ) -> None:
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
        models: type[SQLModel] | list[type[SQLModel]],
        s: Session,
        commit_session: Optional[bool] = True,
    ):
        if isinstance(models, list):
            for m in models:
                SQLModel.metadata.create_all(self._engine, tables=[m.__table__])
        else:
            SQLModel.metadata.create_all(self._engine, tables=[models.__table__])

        self._do_commit(s, commit_session)

    def _do_commit(
        self,
        s: Session,
        commit_session: Optional[bool] = True,
    ):
        if commit_session:
            s.commit()

    def list(self) -> None:
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
