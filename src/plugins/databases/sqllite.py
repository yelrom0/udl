"""
--------------
- sqllite.py -
--------------

Connecting to an embedded sqllite database
"""

# System Imports
from typing import Final, Optional

# Package Imports
from sqlalchemy import engine
from sqlmodel import create_engine, Session, SQLModel

# Local Imports
from .base import DBBase


class SQLLiteEngine(DBBase):
    pass
