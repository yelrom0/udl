"""
-----------
- test.py -
-----------

Test that implemented files are working as I go
"""

# System Imports
import inspect
import uuid

# Package Imports
from sqlmodel import SQLModel, table, Field

# Local Imports
from src.plugins.databases.sqllite import SQLLiteEngine


class TestModel1(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    msg: str = "TestModel1"


class TestModel2(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    msg: str = "TestModel2"


def test_sql_lite():
    """
    TODO: Adding and Transactions are broken (when adding with no commit), need to fix
    """
    sql_lite: SQLLiteEngine = SQLLiteEngine()
    # should be empty
    print(f"Should be empty:\n{sql_lite.list()}")
    sql_lite.add(models=TestModel1(), commit_session=False)
    print(f"Should be empty:\n {sql_lite.list()}")
    sql_lite.add(models=TestModel1)
    print(f"Should contain TestModel1:\n{sql_lite.list()}")
    sql_lite.drop(TestModel1)
    print(f"Should be empty:\n{sql_lite.list()}")
    sql_lite.add([TestModel1(), TestModel2()])
    print(f"Should contain TestModel1 + TestModel2:\n{sql_lite.list()}")
    sql_lite.drop(TestModel1)
    print(f"Should contain TestModel2:\n{sql_lite.list()}")
    sql_lite.drop(TestModel2)
    print(f"Should be empty:\n{sql_lite.list()}")
    # print(inspect.getsource(sql_lite.add))


if __name__ == "__main__":
    # print("I am printing")
    test_sql_lite()
