"""
---------------
- __init__.py -
---------------

udl's interfaces: the CLI (Typer), the HTTP API (FastAPI on Granian) and the MCP server. Each one
only turns its input into an operation's request, calls UDLService and shows the response, so
they offer the same operations (tests/interface/test_parity.py).
"""
