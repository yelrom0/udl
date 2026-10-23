"""
---------------------
- test_downloads.py -
---------------------

udl get (and its options), udl history and udl jobs show / cancel
"""

# System Imports
import uuid
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.cli import CLI
from support.engine import connection
from support.interfaces import A, B, Harness, media
from udl.core.jobs import InstanceLock
from udl.core.models import DownloadBatch, DownloadList, JobInfo
from udl.db.manager import DBManager
from udl.db.models import DownloadQuery, DownloadRecord, JobStatus, Site, Strategy, StrategySource
from udl.errors import ErrorCode

GONE = "https://example.com/gone"


def batch(stdout: str) -> DownloadBatch:
    return DownloadBatch.model_validate_json(stdout)


def history(cli: CLI, *args: str) -> DownloadList:
    result = cli("history", "--json", *args)
    assert result.exit_code == 0, result.output
    return DownloadList.model_validate_json(result.stdout)


# ---------------------------------------------------------------------------------------------
# udl get
# ---------------------------------------------------------------------------------------------


def test_get_says_where_each_download_went(cli: CLI, tmp_path: Path) -> None:
    result = cli("get", A, B)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith(f"✓ {A} → {tmp_path / 'downloads' / 'a.mp4'}")
    assert "(1 byte, http)" in lines[0]
    assert lines[1].startswith(f"✓ {B} → ")


def test_get_says_why_a_download_failed_and_exits_with_1(cli: CLI) -> None:
    result = cli("get", A, GONE)

    assert result.exit_code == 1
    assert f"✓ {A}" in result.stdout
    assert f"✗ {GONE}: " in result.stdout
    assert "no_strategy_found" in result.stdout


def test_get_json_is_the_batch_with_the_errors(cli: CLI) -> None:
    result = cli("get", A, GONE, "--json")

    assert result.exit_code == 1
    finished, failed = batch(result.stdout).jobs
    assert finished.status is JobStatus.FINISHED
    assert failed.error is not None
    assert failed.error.code is ErrorCode.NO_STRATEGY_FOUND


def test_get_reads_urls_from_a_file(cli: CLI, tmp_path: Path) -> None:
    listed = tmp_path / "urls.txt"
    listed.write_text(f"# to download\n{B}\n\n   \n")

    result = cli("get", A, "-i", listed, "--json")

    assert result.exit_code == 0, result.output
    assert [job.url for job in batch(result.stdout).jobs] == [A, B]


def test_get_reads_urls_from_stdin(cli: CLI) -> None:
    result = cli("get", "-i", "-", "--json", stdin=f"{A}\n{B}\n")

    assert result.exit_code == 0, result.output
    assert [job.url for job in batch(result.stdout).jobs] == [A, B]


def test_get_without_urls_is_a_usage_error(cli: CLI) -> None:
    result = cli("get", "-i", "-", stdin="# nothing\n")

    assert result.exit_code == 2
    assert "No URLs" in result.stderr


def test_get_passes_its_options_on(cli: CLI, tmp_path: Path) -> None:
    result = cli(
        "get", A, "--tool", "http", "-o", tmp_path / "out", "-t", "{title}.{ext}", "--json"
    )

    assert result.exit_code == 0, result.output
    [job] = batch(result.stdout).jobs
    assert (job.tool, job.output_dir) == ("http", tmp_path / "out")
    assert [attempt.source for attempt in job.attempts] == ["request"]


@pytest.mark.parametrize("template", ["../{title}", "/abs/{title}", "{title!r}"])
def test_get_with_a_bad_template_is_a_usage_error(cli: CLI, template: str) -> None:
    result = cli("get", A, "-t", template)

    assert result.exit_code == 2
    assert "filename_template" in result.stderr


def test_get_with_an_unknown_tool_fails(cli: CLI) -> None:
    result = cli("get", A, "--tool", "nope")

    assert result.exit_code == 1
    assert "nope" in result.stderr


def test_get_reprobe_ignores_what_worked_before(cli: CLI, harness: Harness) -> None:
    async def scraper_works_there(db: DBManager) -> None:
        await db.upsert_site(Site(key="example.com"))
        await db.upsert_strategy(
            Strategy(site_key="example.com", tool="scraper", source=StrategySource.MANUAL)
        )

    harness.seed(scraper_works_there)

    stored = batch(cli("get", A, "--json").stdout)
    reprobed = batch(cli("get", A, "--reprobe", "--json").stdout)

    assert [a.tool for a in stored.jobs[0].attempts] == ["scraper", "http"]
    assert [a.tool for a in reprobed.jobs[0].attempts] == ["http"]


def test_get_no_record_remembers_nothing(cli: CLI) -> None:
    result = cli("get", A, "--no-record")

    assert result.exit_code == 0, result.output
    assert history(cli).downloads == []
    assert cli("sites", "show", "example.com").exit_code == 1


def test_get_db_keeps_to_those_databases(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path, connections=[connection("local", primary=True), connection("other", priority=1)]
    )
    cli = CLI(harness)

    result = cli("get", A, "--db", "other")

    assert result.exit_code == 0, result.output

    async def where(db: DBManager) -> None:
        assert len(await db.database("other").list_downloads(DownloadQuery())) == 1
        assert await db.database("local").list_downloads(DownloadQuery()) == []

    harness.seed(where)


def test_get_with_an_unknown_database_fails(cli: CLI) -> None:
    result = cli("get", A, "--db", "nope")

    assert result.exit_code == 1
    assert "nope" in result.stderr


# ---------------------------------------------------------------------------------------------
# udl history
# ---------------------------------------------------------------------------------------------


def test_history_lists_the_downloads_newest_first(tmp_path: Path) -> None:
    other = "https://other.org/c"
    cli = CLI(Harness(tmp_path, tools=media({A: "a", other: "c"})))
    cli("get", A)
    cli("get", other)
    cli("get", GONE)

    result = cli("history")

    assert result.exit_code == 0, result.output
    shown = result.stdout
    assert shown.index(GONE) < shown.index(other) < shown.index(A)
    assert [job.url for job in history(cli).downloads] == [GONE, other, A]
    assert [job.url for job in history(cli, "--site", "www.other.org").downloads] == [other]
    assert [job.url for job in history(cli, "--status", "failed").downloads] == [GONE]
    assert [job.url for job in history(cli, "-n", "1", "--offset", "1").downloads] == [other]


def test_history_with_no_downloads(cli: CLI) -> None:
    result = cli("history")

    assert result.exit_code == 0
    assert "No downloads" in result.stdout


def test_history_with_a_bad_status_is_a_usage_error(cli: CLI) -> None:
    assert cli("history", "--status", "nope").exit_code == 2


# ---------------------------------------------------------------------------------------------
# udl jobs
# ---------------------------------------------------------------------------------------------


def test_jobs_show_shows_a_download_and_why_it_failed(cli: CLI) -> None:
    [job] = batch(cli("get", GONE, "--json").stdout).jobs

    result = cli("jobs", "show", str(job.id))

    assert result.exit_code == 0, result.output
    assert GONE in result.stdout
    assert "failed" in result.stdout
    assert "no_strategy_found" in result.stdout
    assert "udl probe" in result.stdout  # (the error's hint)
    shown = JobInfo.model_validate_json(cli("jobs", "show", str(job.id), "--json").stdout)
    assert (shown.id, shown.status) == (job.id, JobStatus.FAILED)


def test_jobs_show_of_a_finished_download(cli: CLI) -> None:
    [job] = batch(cli("get", A, "--json").stdout).jobs

    result = cli("jobs", "show", str(job.id))

    assert result.exit_code == 0, result.output
    assert "finished" in result.stdout
    assert "a.mp4" in result.stdout


def test_jobs_show_of_an_unknown_download_fails(cli: CLI) -> None:
    result = cli("jobs", "show", str(uuid.uuid4()))

    assert result.exit_code == 1
    assert "No download" in result.stderr
    assert "udl history" in result.stderr


def test_jobs_show_of_something_that_is_not_an_id_is_a_usage_error(cli: CLI) -> None:
    assert cli("jobs", "show", "nope").exit_code == 2


def test_jobs_cancel_leaves_a_download_that_has_ended_as_it_is(cli: CLI) -> None:
    [job] = batch(cli("get", A, "--json").stdout).jobs

    result = cli("jobs", "cancel", str(job.id), "--json")

    assert result.exit_code == 0, result.output
    [shown] = DownloadList.model_validate_json(result.stdout).downloads
    assert shown.status is JobStatus.FINISHED
    assert A in cli("jobs", "cancel", str(job.id)).stdout


def test_jobs_cancel_of_a_download_another_udl_runs_says_so(
    cli: CLI, harness: Harness, tmp_path: Path
) -> None:
    record = DownloadRecord(url=A, site_key="example.com", status=JobStatus.RUNNING)
    harness.seed(lambda db: db.add_download(record))
    other = InstanceLock(tmp_path / "udl.lock")  # (the other udl, running it)
    other.acquire()
    other.share()

    result = cli("jobs", "cancel", str(record.id))
    shown = cli("history")
    other.release()

    assert result.exit_code == 1
    assert "another udl" in result.stderr
    assert "running" in shown.stdout


def test_jobs_cancel_of_an_unknown_download_fails(cli: CLI) -> None:
    assert cli("jobs", "cancel", str(uuid.uuid4())).exit_code == 1
