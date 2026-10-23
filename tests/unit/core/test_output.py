"""
------------------
- test_output.py -
------------------

Where downloads go: the output dir's precedence and the filename template
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest
from hypothesis import given
from hypothesis import strategies as st

# Local Imports
from udl.core.output import filename_template, resolve_output_dir
from udl.plugins.downloaders.base import DownloadContext, render_template

REQUEST = Path("/srv/request")
PLUGIN = Path("/srv/plugin")
SITE = Path("/srv/site")
DEFAULT = Path("/srv/default")


@pytest.mark.parametrize(
    ("request_dir", "plugin_dir", "site_dir", "chosen"),
    [
        (REQUEST, PLUGIN, SITE, REQUEST),
        (None, PLUGIN, SITE, PLUGIN),
        (None, None, SITE, SITE),
        (None, None, None, DEFAULT),
    ],
)
def test_the_output_dir_is_the_request_s_then_the_site_plugin_s_then_the_site_s_then_the_config_s(
    request_dir: Path | None, plugin_dir: Path | None, site_dir: Path | None, chosen: Path
) -> None:
    assert (
        resolve_output_dir(request=request_dir, plugin=plugin_dir, site=site_dir, default=DEFAULT)
        == chosen
    )


def test_the_output_dir_expands_the_home_dir_and_environment_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("UDL_TEST_MEDIA", "media")

    assert resolve_output_dir(request=Path("~/$UDL_TEST_MEDIA/clips"), default=DEFAULT) == (
        tmp_path / "media" / "clips"
    )


def test_a_relative_output_dir_is_relative_to_the_working_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    assert resolve_output_dir(request=Path("clips"), default=DEFAULT) == tmp_path / "clips"


def test_resolving_the_output_dir_creates_nothing(tmp_path: Path) -> None:
    resolve_output_dir(request=tmp_path / "new", default=DEFAULT)

    assert not (tmp_path / "new").exists()


def test_the_filename_template_gets_the_site_and_leaves_the_rest_to_the_tool() -> None:
    assert filename_template("{site}/{title} [{id}].{ext}", "example.com") == (
        "example.com/{title} [{id}].{ext}"
    )


@pytest.mark.parametrize(
    ("key", "filled"),
    [
        ("::1", "::1/{title}.{ext}"),  # an IPv6 host is its own key
        ("{title}", "{{title}}/{title}.{ext}"),  # braces stay text
        ("..", "_/{title}.{ext}"),
        ("a/b", "a_b/{title}.{ext}"),
    ],
)
def test_the_site_never_adds_fields_or_folders(key: str, filled: str) -> None:
    assert filename_template("{site}/{title}.{ext}", key) == filled


templates = st.sampled_from(
    [
        "{site}/{title}.{ext}",
        "{title}.{ext}",
        "{site} - {title} [{id}].{ext}",
        "media/{site}/{{literal}} {title}.{ext}",
        "100% {site}.{ext}",
    ]
)
values = st.dictionaries(st.sampled_from(["title", "id", "ext"]), st.text())


@given(templates, st.text(), values)
def test_filling_in_the_site_first_renders_the_same_path(
    template: str, site: str, fields: dict[str, str]
) -> None:
    filled = filename_template(template, site)

    assert render_template(filled, fields) == render_template(template, {**fields, "site": site})


@given(templates, st.text(), values)
def test_a_filled_template_is_valid_and_never_leaves_the_output_dir(
    template: str, site: str, fields: dict[str, str]
) -> None:
    filled = filename_template(template, site)
    ctx = DownloadContext(
        url="https://example.com/", output_dir=Path("/out"), filename_template=filled
    )
    rendered = Path(render_template(filled, fields))

    assert ctx.filename_template == filled
    assert not rendered.is_absolute()
    assert ".." not in rendered.parts
