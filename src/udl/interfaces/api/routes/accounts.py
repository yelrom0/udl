"""
---------------
- accounts.py -
---------------

GET /accounts/callback: where an OAuth authorization (connecting an account, connect_account)
sends the browser back to. It needs no API key, as the browser has none: the authorization's
one-time state is what it is checked by. It answers with a page saying the account is
connected (or why it is not), or sends the browser on to where connecting it asked (return_to).
"""

# System Imports
import html

# Package Imports
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

# Local Imports
from udl.core.service import UDLService
from udl.errors import NotAllowed, UDLError


def _page(message: str, hint: str | None = None) -> str:
    more = f"<p>{html.escape(hint)}</p>" if hint else ""
    return f"<!doctype html><title>udl</title><p>{html.escape(message)}</p>{more}"


async def callback(request: Request) -> Response:
    """Finish connecting the account an OAuth authorization was for."""
    service: UDLService = request.app.state.service
    try:
        completed = await service.accounts.complete(dict(request.query_params))
    except UDLError as e:
        status = 403 if isinstance(e, NotAllowed) else 400
        return HTMLResponse(
            _page(f"udl could not connect the account: {e.message}", e.hint), status_code=status
        )
    if completed.return_to is not None:
        return RedirectResponse(completed.return_to, status_code=303)
    return HTMLResponse(_page(f"{completed.account.ref} is connected. You can close this tab."))
