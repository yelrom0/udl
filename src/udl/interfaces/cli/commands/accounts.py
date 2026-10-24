"""
---------------
- accounts.py -
---------------

udl accounts: the accounts of integrations udl connects to. `udl accounts add` connects one: in
the browser for OAuth (udl opens the page, printing it too, and waits for its answer on
127.0.0.1), with tokens (asked for, hidden, or read from stdin as JSON), or with its options
alone (a source that needs no login)
"""

# System Imports
import json
import webbrowser
from typing import Annotated, Any

# Package Imports
import typer

# Local Imports
from udl.core.service import UDLService
from udl.integrations.accounts import token_model
from udl.integrations.models import AccountInfo, ConnectAccountRequest
from udl.interfaces.cli import render
from udl.interfaces.cli.common import (
    JsonOption,
    key_values,
    parse,
    run,
    runs,
    show,
    stderr,
    usage_error,
)
from udl.plugins.integrations.base import ConnectMethod, Integration

app = typer.Typer(
    help="Accounts: the logins (and sources) of the integrations udl connects to.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)


@app.command("add")
@runs("connect_account")
def add(
    ctx: typer.Context,
    integration: Annotated[
        str, typer.Argument(help="The integration (e.g. gmail).", show_default=False)
    ],
    name: Annotated[
        str, typer.Argument(help="What to call the account (e.g. work).", show_default=False)
    ],
    access: Annotated[
        list[str] | None,
        typer.Option(
            "--access",
            help="What to connect it for, e.g. read,send (or repeated); by default the "
            "integration's first access level.",
            show_default=False,
        ),
    ] = None,
    options: Annotated[
        list[str] | None,
        typer.Option(
            "-O",
            "--option",
            help="One of its options, KEY=VALUE (VALUE is JSON, else text); repeat for more.",
            metavar="KEY=VALUE",
            show_default=False,
        ),
    ] = None,
    method: Annotated[
        ConnectMethod | None,
        typer.Option(
            "--method",
            help="How to connect it (by default the integration's usual way).",
            show_default=False,
        ),
    ] = None,
    reconnect: Annotated[
        bool,
        typer.Option(
            "--reconnect",
            help="Connect an account that was connected before again, keeping its options and "
            "subscriptions.",
        ),
    ] = False,
    tokens_stdin: Annotated[
        bool,
        typer.Option(
            "--tokens-stdin",
            help="Read the tokens from stdin as one JSON object, rather than asking for each.",
        ),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Connect an account of an integration, named so its operations, events and subscriptions
    can say which (`gmail:work`).

    For OAuth, udl opens the authorization page in the browser (and prints it) and waits for
    its answer. Tokens (e.g. a Slack app's) are asked for, hidden (or read from stdin with
    --tokens-stdin), and kept in the secret store. A source that needs no login is connected
    with its options (-O KEY=VALUE)."""
    levels = [level.strip() for given in access or [] for level in given.split(",") if level]

    async def work(service: UDLService) -> AccountInfo:
        plugin = service.integrations.plugin(integration)
        chosen = method or (ConnectMethod.TOKENS if tokens_stdin else plugin.connect[0])
        tokens = (
            _tokens(plugin, from_stdin=tokens_stdin) if chosen is ConnectMethod.TOKENS else None
        )
        request = parse(
            ConnectAccountRequest,
            integration=integration,
            name=name,
            method=chosen,
            access=levels,
            options=key_values(options or []),
            tokens=tokens,
            reconnect=reconnect,
        )
        connection = await service.connect_account(request)
        url = connection.authorization_url
        if url is None:
            return connection.account
        stderr().print(
            f"Connecting {connection.account.account}: answer this page in the browser (udl "
            f"waits for 10 minutes):\n{url}",
            soft_wrap=True,
        )
        webbrowser.open(url)
        return AccountInfo.of(await service.accounts.finished(connection.account.id))

    show(run(ctx, work), as_json, render.account)


def _tokens(plugin: type[Integration[Any, Any]], *, from_stdin: bool) -> dict[str, Any]:
    """The tokens to connect an account of `plugin` with: asked for, or read from stdin."""
    model = token_model(plugin)  # (raises PluginError)
    if from_stdin:
        try:
            found = json.loads(typer.get_text_stream("stdin").read())
        except json.JSONDecodeError as e:
            usage_error(f"The tokens on stdin are not JSON: {e}")
        if not isinstance(found, dict):
            usage_error('The tokens on stdin are one JSON object, e.g. {"bot_token": "..."}')
        return dict(found)  # pyright: ignore[reportUnknownArgumentType]
    return {
        field: typer.prompt((info.description or field).rstrip("."), hide_input=True, err=True)
        for field, info in model.model_fields.items()
    }
