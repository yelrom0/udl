"""
-------------
- sqlite.py -
-------------

Connecting to an embedded SQLite database
"""

# System Imports
from typing import Final, Optional

# Package Imports
from sqlalchemy import engine
from sqlmodel import create_engine, Session, SQLModel

# Local Imports
from .sqlalchemy import SQLAlchemyDatabase


class SQLitePlugin(SQLAlchemyDatabase):
    pass
