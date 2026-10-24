"""
-----------------
- test_script.py -
-----------------

The script action: runs a program from scripts_dir (resolved, so `..` and links cannot leave
it), never through a shell, with the event as JSON on its stdin and in a file of its own, an
environment of udl's making (never udl's own, which may hold secrets), at most 64 KiB of
output, and the end of its stderr as why it failed
"""

# System Imports
import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Package Imports
import pytest
from pydantic import JsonValue

# Local Imports
from support.interfaces import Harness
from udl.automations.events import Event, EventTypes
from udl.config import AutomationsSettings, Settings
from udl.core.service import UDLService
from udl.db.models import JsonObject
from udl.errors import ActionFailed, ConfigError, NotAllowed
from udl.plugins.actions.base import ActionContext, RunLog
from udl.plugins.actions.script import MAX_OUTPUT, ScriptAction
from udl.process import CancelToken

EVENT = Event(
    id=uuid4(),
    type="custom.hello",
    source="api",
    occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
    subject="Hello",
    data={"name": "x"},
)


def script(folder: Path, name: str, body: str, *, executable: bool = True) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755 if executable else 0o644)
    return path


@pytest.fixture
def scripts(tmp_path: Path) -> Path:
    folder = tmp_path / "scripts"
    folder.mkdir()
    return folder


@pytest.fixture
async def service(tmp_path: Path, scripts: Path) -> AsyncIterator[UDLService]:
    harness = Harness(tmp_path, automations=AutomationsSettings(scripts_dir=scripts))
    async with harness.service() as found:
        yield found


@pytest.fixture
def plugin(service: UDLService) -> ScriptAction:
    return ScriptAction(service)


def context(
    plugin: ScriptAction, options: JsonObject, event: Event | None = EVENT
) -> ActionContext[Any]:
    service = plugin.service
    return ActionContext(
        run_id=uuid4(),
        event=event,
        options=plugin.options(options),
        service=service,
        secrets=service.secrets,
        clock=service.clock,
        cancel=CancelToken(),
        log=RunLog(),
        action="hello",
    )


async def run(plugin: ScriptAction, options: JsonObject, event: Event | None = EVENT) -> Any:
    return (await plugin.run(context(plugin, options, event))).output


# ---------------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------------


async def test_its_json_output_is_the_run_s_output(plugin: ScriptAction, scripts: Path) -> None:
    script(scripts, "ok.sh", 'echo \'{"filed": true, "summary": "Filed it"}\'')

    done = await plugin.run(context(plugin, {"command": ["ok.sh"]}))

    assert done.output == {"filed": True, "summary": "Filed it"}
    assert done.summary == "Filed it"


async def test_its_text_output_is_the_run_s_output(plugin: ScriptAction, scripts: Path) -> None:
    script(scripts, "hi.sh", "echo 'Hello there'\necho 'and more'")

    done = await plugin.run(context(plugin, {"command": ["hi.sh"]}))

    assert done.output == "Hello there\nand more\n"
    assert done.summary == "Hello there"


async def test_the_event_comes_on_stdin(plugin: ScriptAction, scripts: Path) -> None:
    script(scripts, "cat.sh", "cat")

    found = await run(plugin, {"command": ["cat.sh"]})

    assert Event.model_validate(found) == EVENT


async def test_stdin_may_be_nothing_or_a_text(plugin: ScriptAction, scripts: Path) -> None:
    script(scripts, "cat.sh", "cat")

    assert await run(plugin, {"command": ["cat.sh"], "stdin": "none"}) == ""
    assert await run(plugin, {"command": ["cat.sh"], "stdin": "Some words"}) == "Some words"


async def test_without_an_event_stdin_is_null(plugin: ScriptAction, scripts: Path) -> None:
    script(scripts, "cat.sh", "cat")

    assert await run(plugin, {"command": ["cat.sh"]}, event=None) is None


async def test_it_never_runs_a_shell(plugin: ScriptAction, scripts: Path, tmp_path: Path) -> None:
    script(scripts, "args.sh", "printf '%s\\n' \"$@\"")
    pwned = tmp_path / "pwned"
    risky = [f"$(touch {pwned})", f"; touch {pwned}", "`id`", "a b", "*"]

    found = await run(plugin, {"command": ["args.sh", *risky]})

    assert found == "".join(f"{argument}\n" for argument in risky)
    assert not pwned.exists()


async def test_its_environment_is_udl_s_making(
    plugin: ScriptAction, scripts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script(scripts, "env.sh", "env")
    monkeypatch.setenv("UDL_API__API_KEY", "s3cret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s3cret")
    monkeypatch.setenv("LANG", "en_GB.UTF-8")

    ctx = context(plugin, {"command": ["env.sh"], "env": {"WHO": "me"}})
    found = (await plugin.run(ctx)).output
    env = dict(line.split("=", 1) for line in str(found).splitlines() if "=" in line)

    assert "s3cret" not in str(found)
    assert env["WHO"] == "me"
    assert env["PATH"] == os.environ["PATH"]
    assert env["HOME"] == os.environ["HOME"]
    assert env["LANG"] == "en_GB.UTF-8"
    assert env["UDL_EVENT_ID"] == str(EVENT.id)
    assert env["UDL_EVENT_TYPE"] == "custom.hello"
    assert env["UDL_RUN_ID"] == str(ctx.run_id)
    assert env["UDL_ACTION"] == "hello"
    assert set(env) <= {
        "WHO", "PATH", "HOME", "LANG", "PWD", "SHLVL", "_",
        "UDL_EVENT_ID", "UDL_EVENT_TYPE", "UDL_EVENT_FILE", "UDL_RUN_ID", "UDL_ACTION",
    }  # fmt: skip


async def test_udl_s_config_file_is_named(tmp_path: Path, scripts: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'[automations]\nscripts_dir = "{scripts}"\n')
    script(scripts, "config.sh", 'echo "$UDL_CONFIG"')
    harness = Harness(tmp_path)

    async with harness.service(Settings.load(config)) as service:
        found = await run(ScriptAction(service), {"command": ["config.sh"]})

    assert found == f"{config}\n"


async def test_the_event_s_file_is_its_own_and_gone_after(
    plugin: ScriptAction, scripts: Path
) -> None:
    script(
        scripts,
        "file.sh",
        'stat -c %a "$UDL_EVENT_FILE"; echo "$UDL_EVENT_FILE"; cat "$UDL_EVENT_FILE"',
    )

    mode, path, *event = str(await run(plugin, {"command": ["file.sh"]})).splitlines()

    assert mode == "600"
    assert Event.model_validate_json("\n".join(event)) == EVENT
    assert not await asyncio.to_thread(Path(path).exists)


async def test_it_runs_in_scripts_dir_unless_told_where(
    plugin: ScriptAction, scripts: Path, tmp_path: Path
) -> None:
    script(scripts, "pwd.sh", "pwd")

    assert await run(plugin, {"command": ["pwd.sh"]}) == f"{scripts}\n"
    assert await run(plugin, {"command": ["pwd.sh"], "cwd": str(tmp_path)}) == f"{tmp_path}\n"


async def test_it_keeps_64_kib_of_output(plugin: ScriptAction, scripts: Path) -> None:
    script(scripts, "lots.sh", "head -c 200000 /dev/zero | tr '\\0' 'a'")

    ctx = context(plugin, {"command": ["lots.sh"]})
    found = (await plugin.run(ctx)).output

    assert MAX_OUTPUT == 64 * 1024
    assert found == "a" * MAX_OUTPUT
    assert "left out" in ctx.log.text


async def test_its_stderr_is_the_run_s_log(plugin: ScriptAction, scripts: Path) -> None:
    script(scripts, "noisy.sh", "echo working >&2\necho done")

    ctx = context(plugin, {"command": ["noisy.sh"]})
    await plugin.run(ctx)

    assert "working" in ctx.log.text


async def test_a_failure_says_the_end_of_stderr(plugin: ScriptAction, scripts: Path) -> None:
    script(
        scripts,
        "fail.sh",
        "head -c 50000 /dev/zero | tr '\\0' 'x' >&2\necho >&2\necho 'the real reason' >&2\nexit 3",
    )

    with pytest.raises(ActionFailed) as raised:
        await run(plugin, {"command": ["fail.sh"]})

    assert "fail.sh exited with 3" in raised.value.message
    assert raised.value.message.endswith("the real reason")
    assert len(raised.value.message) < 2000
    assert raised.value.may_retry is False


# ---------------------------------------------------------------------------------------------
# Only programs in scripts_dir
# ---------------------------------------------------------------------------------------------


async def test_a_program_in_scripts_dir_passes(plugin: ScriptAction, scripts: Path) -> None:
    inside = script(scripts, "sub/x.sh", "true")

    await plugin.check({"command": ["sub/x.sh"]}, None)
    await plugin.check({"command": [str(inside)]}, None)


@pytest.mark.parametrize("named", ["../outside.sh", "{outside}", "sub/../../outside.sh", "link.sh"])
async def test_a_program_outside_scripts_dir_is_refused(
    plugin: ScriptAction, scripts: Path, tmp_path: Path, named: str
) -> None:
    outside = script(tmp_path, "outside.sh", "true")
    (scripts / "link.sh").symlink_to(outside)
    command: list[JsonValue] = [named.format(outside=outside)]

    with pytest.raises(NotAllowed, match="scripts_dir"):
        await plugin.check({"command": command}, None)
    with pytest.raises(ActionFailed, match="scripts_dir"):
        await run(plugin, {"command": command})


@pytest.mark.parametrize(
    ("named", "said"), [("missing.sh", "no program"), ("plain.sh", "executable")]
)
async def test_a_program_that_cannot_run_is_refused(
    plugin: ScriptAction, scripts: Path, named: str, said: str
) -> None:
    script(scripts, "plain.sh", "true", executable=False)

    with pytest.raises(ConfigError, match=said):
        await plugin.check({"command": [named]}, None)
    with pytest.raises(ActionFailed, match=said):
        await run(plugin, {"command": [named]})


async def test_a_program_named_by_a_template_is_checked_when_it_runs(
    plugin: ScriptAction, scripts: Path
) -> None:
    await plugin.check({"command": ["{{ event.data.name }}.sh"]}, None)

    with pytest.raises(ActionFailed, match="no program"):
        await run(plugin, {"command": ["x.sh"]})


async def test_its_arguments_are_checked_with_the_event_type_s_example(
    plugin: ScriptAction, scripts: Path
) -> None:
    script(scripts, "ok.sh", "true")
    finished = EventTypes.default().get("udl.download.finished")

    await plugin.check({"command": ["ok.sh", "{{ event.data.url }}"]}, finished)

    with pytest.raises(ConfigError):
        await plugin.check({"command": ["ok.sh", "{{ event.data.nope }}"]}, finished)


def test_its_options_need_a_command(plugin: ScriptAction) -> None:
    with pytest.raises(ConfigError):
        plugin.options({"command": []})


async def test_a_folder_that_is_not_there_fails_the_run(
    plugin: ScriptAction, scripts: Path, tmp_path: Path
) -> None:
    script(scripts, "ok.sh", "true")

    with pytest.raises(ActionFailed, match="not a folder"):
        await run(plugin, {"command": ["ok.sh"], "cwd": str(tmp_path / "nowhere")})


async def test_a_program_that_cannot_be_run_fails_the_run(
    plugin: ScriptAction, scripts: Path
) -> None:
    no_shebang = scripts / "plain.sh"
    no_shebang.write_bytes(b"\x00\x01 not a program")
    no_shebang.chmod(0o755)

    with pytest.raises(ActionFailed, match="cannot be run"):
        await run(plugin, {"command": ["plain.sh"]})


async def test_a_program_that_went_away_fails_the_run(
    plugin: ScriptAction, scripts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(named: str) -> Path:
        return scripts / "gone.sh"

    monkeypatch.setattr(plugin, "program", gone)

    with pytest.raises(ActionFailed, match="cannot be run"):
        await run(plugin, {"command": ["gone.sh"]})
