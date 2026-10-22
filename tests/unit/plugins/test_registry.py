"""
--------------------
- test_registry.py -
--------------------

PluginRegistry: entry points, local plugin dirs, overrides and broken plugins
"""

# System Imports
import importlib
import logging
import re
import sys
import textwrap
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.plugins import GADGETS, WIDGETS, Gadget, Widget
from udl.errors import PluginError
from udl.plugins.databases.memory import MemoryDatabase
from udl.plugins.databases.sqlite import SQLitePlugin
from udl.plugins.registry import DATABASES, PluginInfo, PluginRegistry, PluginSource

GEAR = """
from support.plugins import Widget

class Gear(Widget):
    name = "gear"

    def spin(self) -> str:
        return "{spin}"
"""

type Install = Callable[[Mapping[str, str], str], str]


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Install]:
    """Install a fake distribution that publishes entry points from one module of its own.

    Takes {"group": "name = attr"} lines and the module's source; `{module}` in either is
    replaced by the module's (unique) name, which is returned.
    """
    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.setattr(sys, "path", [str(site), *sys.path])
    importlib.invalidate_caches()
    modules: list[str] = []

    def install(entry_points: Mapping[str, str], source: str) -> str:
        module = f"udl_test_plugins_{uuid.uuid4().hex}"
        modules.append(module)
        (site / f"{module}.py").write_text(textwrap.dedent(source))
        dist_info = site / f"{module}-1.0.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {module}\nVersion: 1.0\n"
        )
        sections = [
            f"[{group}]\n{line.format(module=module)}\n" for group, line in entry_points.items()
        ]
        (dist_info / "entry_points.txt").write_text("\n".join(sections))
        return module

    yield install
    for module in modules:
        sys.modules.pop(module, None)


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "plugins"
    path.mkdir()
    return path


def write_plugin(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(source))
    return path


def test_a_plugin_published_as_an_entry_point_is_discovered(install: Install) -> None:
    module = install({"udl_test.widgets": "gear = {module}:Gear"}, GEAR.format(spin="installed"))
    registry = PluginRegistry([WIDGETS])

    registry.load_entry_points()

    assert registry.get(WIDGETS, "gear")().spin() == "installed"
    assert registry.plugins() == [
        PluginInfo(
            kind="udl_test.widgets",
            name="gear",
            source=PluginSource.INSTALLED,
            origin=f"{module}:Gear",
        )
    ]


def test_each_kind_only_loads_its_own_entry_point_group(install: Install) -> None:
    install(
        {"udl_test.widgets": "gear = {module}:Gear", "udl_test.other": "cog = {module}:Gear"},
        GEAR.format(spin="installed"),
    )
    registry = PluginRegistry([WIDGETS, GADGETS])

    registry.load_entry_points()

    assert [info.name for info in registry.plugins(WIDGETS)] == ["gear"]
    assert registry.plugins(GADGETS) == []


def test_a_plugin_file_in_a_plugin_dir_is_loaded(plugin_dir: Path) -> None:
    path = write_plugin(plugin_dir, "gears.py", GEAR.format(spin="local"))
    registry = PluginRegistry([WIDGETS])

    registry.load_dir(plugin_dir)

    assert registry.get(WIDGETS, "gear")().spin() == "local"
    assert registry.plugins() == [
        PluginInfo(
            kind="udl_test.widgets", name="gear", source=PluginSource.LOCAL, origin=str(path)
        )
    ]


def test_a_plugin_file_registers_every_concrete_plugin_class_it_defines(plugin_dir: Path) -> None:
    write_plugin(
        plugin_dir,
        "many.py",
        """
        from abc import abstractmethod

        from support.plugins import Gadget, Widget


        class Unrelated:
            name = "unrelated"


        class AbstractWidget(Widget):
            @abstractmethod
            def extra(self) -> None: ...


        class Sprocket(Widget):
            name = "sprocket"

            def spin(self) -> str:
                return "sprocket"


        class Doohickey(Gadget):
            name = "doohickey"
        """,
    )
    registry = PluginRegistry([WIDGETS, GADGETS])

    registry.load_dir(plugin_dir)

    assert [(info.kind, info.name) for info in registry.plugins()] == [
        ("udl_test.gadgets", "doohickey"),
        ("udl_test.widgets", "sprocket"),
    ]
    assert registry.problems == []


def test_classes_a_plugin_file_only_imports_are_not_registered_again(plugin_dir: Path) -> None:
    write_plugin(plugin_dir, "gears.py", GEAR.format(spin="local"))
    write_plugin(
        plugin_dir,
        "subclass.py",
        """
        from support.plugins import Widget


        class Base(Widget):
            name = "base"

            def spin(self) -> str:
                return "base"
        """,
    )
    registry = PluginRegistry([WIDGETS])

    registry.load_dir(plugin_dir)

    assert [info.name for info in registry.plugins()] == ["base", "gear"]


def test_a_local_plugin_overrides_an_installed_one_and_logs_a_warning(
    install: Install, plugin_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    module = install({"udl_test.widgets": "gear = {module}:Gear"}, GEAR.format(spin="installed"))
    path = write_plugin(plugin_dir, "gears.py", GEAR.format(spin="local"))

    registry = PluginRegistry.discover([plugin_dir], kinds=[WIDGETS])

    assert registry.get(WIDGETS, "gear")().spin() == "local"
    assert [info.source for info in registry.plugins()] == [PluginSource.LOCAL]
    [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert f"{module}:Gear" in record.getMessage()
    assert str(path) in record.getMessage()


def test_later_plugin_dirs_override_earlier_ones(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_plugin(first, "gears.py", GEAR.format(spin="first"))
    write_plugin(second, "gears.py", GEAR.format(spin="second"))

    registry = PluginRegistry.discover([first, second], kinds=[WIDGETS])

    assert registry.get(WIDGETS, "gear")().spin() == "second"


def test_a_missing_plugin_dir_is_skipped(tmp_path: Path) -> None:
    registry = PluginRegistry.discover([tmp_path / "missing"], kinds=[WIDGETS])

    assert registry.plugins() == []
    assert registry.problems == []


@pytest.mark.parametrize(
    ("source", "error"),
    [
        ("raise RuntimeError('boom')", "RuntimeError: boom"),
        ("def broken(:\n", "SyntaxError"),
        ("import udl_no_such_module", "ModuleNotFoundError"),
    ],
)
def test_a_broken_plugin_file_is_reported_not_fatal(
    plugin_dir: Path, caplog: pytest.LogCaptureFixture, source: str, error: str
) -> None:
    broken = write_plugin(plugin_dir, "broken.py", source)
    write_plugin(plugin_dir, "gears.py", GEAR.format(spin="local"))
    registry = PluginRegistry([WIDGETS])

    registry.load_dir(plugin_dir)

    assert registry.get(WIDGETS, "gear")().spin() == "local"
    [problem] = registry.problems
    assert problem.origin == str(broken)
    assert error in problem.error
    assert error in caplog.text
    assert "broken" not in "".join(sys.modules)


@pytest.mark.parametrize(
    ("entry_point", "error"),
    [
        ("gear = udl_no_such_module:Gear", "ModuleNotFoundError"),
        ("gear = {module}:Missing", "AttributeError"),
        ("gear = {module}:NotAWidget", "is not a udl_test.widgets plugin"),
        ("gear = {module}:NOT_A_CLASS", "1 is not a udl_test.widgets plugin"),
        ("cog = {module}:Gear", "is named 'gear'"),
    ],
)
def test_a_broken_entry_point_is_reported_not_fatal(
    install: Install, caplog: pytest.LogCaptureFixture, entry_point: str, error: str
) -> None:
    module = install(
        {"udl_test.widgets": entry_point},
        GEAR.format(spin="") + "\nclass NotAWidget: ...\nNOT_A_CLASS = 1\n",
    )
    registry = PluginRegistry([WIDGETS])

    registry.load_entry_points()

    assert registry.plugins() == []
    [problem] = registry.problems
    assert problem.kind == "udl_test.widgets"
    assert problem.origin == entry_point.split(" = ")[1].format(module=module)
    assert error in problem.error
    assert error in caplog.text


def test_a_plugin_class_without_a_name_is_reported(plugin_dir: Path) -> None:
    path = write_plugin(
        plugin_dir,
        "nameless.py",
        """
        from support.plugins import Gadget


        class Nameless(Gadget): ...
        """,
    )
    registry = PluginRegistry([GADGETS])

    registry.load_dir(plugin_dir)

    assert registry.plugins() == []
    [problem] = registry.problems
    assert (problem.kind, problem.origin) == ("udl_test.gadgets", str(path))
    assert "Nameless has no name" in problem.error


def test_register_adds_a_plugin_class_directly() -> None:
    class Cog(Gadget):
        name = "cog"

    registry = PluginRegistry([GADGETS])

    registry.register(GADGETS, Cog)

    assert registry.get(GADGETS, "cog") is Cog
    [info] = registry.plugins()
    assert (info.source, info.origin) == (PluginSource.LOCAL, f"{__name__}:{Cog.__qualname__}")


def test_register_rejects_a_class_of_another_kind() -> None:
    class Cog(Gadget):
        name = "cog"

    registry = PluginRegistry([WIDGETS, GADGETS])

    with pytest.raises(PluginError, match=re.escape("is not a udl_test.widgets plugin")):
        registry.register(WIDGETS, Cog)  # pyright: ignore[reportArgumentType]


def test_getting_an_unknown_plugin_raises_plugin_error_naming_the_known_ones() -> None:
    class Sprocket(Widget):
        name = "sprocket"

        def spin(self) -> str:
            return "sprocket"

    registry = PluginRegistry([WIDGETS])
    registry.register(WIDGETS, Sprocket)

    with pytest.raises(
        PluginError, match=re.escape("No udl_test.widgets plugin named 'gear'")
    ) as excinfo:
        registry.get(WIDGETS, "gear")

    assert excinfo.value.hint is not None
    assert "sprocket" in excinfo.value.hint


def test_getting_a_plugin_of_a_kind_the_registry_does_not_know_raises_plugin_error() -> None:
    registry = PluginRegistry([WIDGETS])

    with pytest.raises(PluginError, match=re.escape("udl_test.gadgets")):
        registry.get(GADGETS, "cog")


def test_udls_own_plugins_are_registered_as_builtins() -> None:
    registry = PluginRegistry.discover()

    assert registry.get(DATABASES, "memory") is MemoryDatabase
    assert registry.get(DATABASES, "sqlite") is SQLitePlugin
    assert {info.source for info in registry.plugins(DATABASES)} == {PluginSource.BUILTIN}
    assert registry.problems == []
