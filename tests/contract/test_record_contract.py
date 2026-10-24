"""
---------------------------
- test_record_contract.py -
---------------------------

The behaviour every database plugin must have with records (udl.db.records): the kinds of data
automations keep (accounts, subscriptions, events...), and the leases that keep two udls from
doing the same work. Each test runs once per plugin in conftest.py's CONNECTIONS.
"""

# System Imports
import asyncio
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

# Package Imports
import pytest

# Local Imports
from support.records import Detail, Note, NoteStatus, Tally
from udl.db.records import Lease, Record, RecordQuery, record_kinds
from udl.errors import DuplicateKey
from udl.plugins.databases.base import DatabasePlugin

T0 = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)
LONG = timedelta(minutes=5)
MINUTE = timedelta(minutes=1)


def note(topic: str = "inbox", slug: str | None = None, **fields: object) -> Note:
    return Note.model_validate({"topic": topic, "slug": slug or uuid4().hex, **fields})


async def stored(db: DatabasePlugin, *notes: Note) -> list[Note]:
    return [await db.upsert_record(found) for found in notes]


# ---------------------------------------------------------------------------------------------
# Storing records
# ---------------------------------------------------------------------------------------------


async def test_an_unknown_record_is_none(db: DatabasePlugin) -> None:
    assert await db.get_record(Note, uuid4()) is None


async def test_a_stored_record_round_trips(db: DatabasePlugin) -> None:
    record = note(
        status=NoteStatus.DONE,
        count=-(2**40),
        flag=True,
        owner=uuid4(),
        # Stored as the same instant, whatever the timezone.
        due=T0.astimezone(timezone(timedelta(hours=-3))),
        ref="r1",
        body={"list": [1, None, True, 2.5], "nested": {"a": "b"}},
        detail=Detail(words=["x", "y"], weight=0.5),
        created_at=T0,
        updated_at=T0 + timedelta(seconds=1),
    )

    assert await db.upsert_record(record) == record
    found = await db.get_record(Note, record.id)

    assert found == record
    assert isinstance(found, Note)
    assert isinstance(found.detail, Detail)  # (JSON data comes back as its model)


async def test_upserting_a_stored_record_replaces_it_keeping_when_it_was_created(
    db: DatabasePlugin,
) -> None:
    [original] = await stored(db, note(count=1, created_at=T0, updated_at=T0))
    later = T0 + timedelta(days=1)

    updated = await db.upsert_record(
        original.model_copy(update={"count": 2, "created_at": later, "updated_at": later})
    )

    # (The caller stamps the times, from its clock: DBManager.)
    assert (updated.id, updated.count, updated.created_at) == (original.id, 2, T0)
    assert updated.updated_at == later
    assert await db.get_record(Note, original.id) == updated
    assert len(await db.list_records(Note, RecordQuery())) == 1


async def test_records_of_each_kind_are_kept_apart(db: DatabasePlugin) -> None:
    [kept] = await stored(db, note())
    tally = await db.upsert_record(Tally(name="t", total=3))

    assert await db.list_records(Note, RecordQuery()) == [kept]
    assert await db.list_records(Tally, RecordQuery()) == [tally]
    assert await db.get_record(Tally, kept.id) is None


async def test_deleting_a_record(db: DatabasePlugin) -> None:
    gone, kept = await stored(db, note(), note())

    assert await db.delete_record(Note, gone.id) is True
    assert await db.delete_record(Note, gone.id) is False

    assert await db.get_record(Note, gone.id) is None
    assert await db.list_records(Note, RecordQuery()) == [kept]


async def test_changing_a_record_does_not_change_the_stored_one(db: DatabasePlugin) -> None:
    record = note(body={"a": [1]})
    returned = await db.upsert_record(record)

    record.body["a"] = [2]
    returned.body["a"] = [3]
    [listed] = await db.list_records(Note, RecordQuery())
    listed.body["a"] = [4]

    found = await db.get_record(Note, record.id)
    assert found is not None
    assert found.body == {"a": [1]}


# ---------------------------------------------------------------------------------------------
# Unique keys
# ---------------------------------------------------------------------------------------------


async def test_a_record_with_another_s_unique_key_is_refused(db: DatabasePlugin) -> None:
    [first] = await stored(db, note("inbox", "a", count=1))

    with pytest.raises(DuplicateKey, match="test_note"):
        await db.upsert_record(note("inbox", "a", count=2))

    assert await db.list_records(Note, RecordQuery()) == [first]


async def test_changing_a_record_to_another_s_unique_key_is_refused(db: DatabasePlugin) -> None:
    first, second = await stored(db, note("inbox", "a"), note("inbox", "b"))

    with pytest.raises(DuplicateKey):
        await db.upsert_record(second.model_copy(update={"slug": "a"}))

    assert await db.get_record(Note, second.id) == second
    assert await db.get_record(Note, first.id) == first


async def test_a_unique_key_is_every_field_of_it_together(db: DatabasePlugin) -> None:
    records = await stored(db, note("inbox", "a"), note("outbox", "a"), note("inbox", "b"))

    assert len(await db.list_records(Note, RecordQuery())) == len(records)


async def test_records_whose_unique_field_is_empty_never_clash(db: DatabasePlugin) -> None:
    await stored(db, note(ref=None), note(ref=None), note(ref="x"))

    with pytest.raises(DuplicateKey):
        await db.upsert_record(note(ref="x"))

    assert len(await db.list_records(Note, RecordQuery())) == 3


async def test_a_record_upserted_again_keeps_its_own_unique_key(db: DatabasePlugin) -> None:
    [record] = await stored(db, note("inbox", "a"))

    again = await db.upsert_record(record.model_copy(update={"count": 9}))

    assert again.count == 9


# ---------------------------------------------------------------------------------------------
# Listing records
# ---------------------------------------------------------------------------------------------


async def test_records_are_listed_newest_first_to_the_microsecond(db: DatabasePlugin) -> None:
    second = T0.replace(microsecond=0)
    times = [
        second,
        second + timedelta(microseconds=1),
        second + timedelta(milliseconds=500),
        (second + timedelta(seconds=1)).astimezone(timezone(timedelta(hours=-5))),
    ]
    records = [note(created_at=at) for at in times]
    await stored(db, *reversed(records))

    assert await db.list_records(Note, RecordQuery()) == records[::-1]


async def test_records_made_at_the_same_time_are_listed_by_id(db: DatabasePlugin) -> None:
    records = await stored(db, *(note(created_at=T0) for _ in range(5)))

    listed = await db.list_records(Note, RecordQuery())

    assert listed == sorted(records, key=lambda record: record.id, reverse=True)


@pytest.mark.parametrize(
    ("where", "wanted"),
    [
        ({"topic": "outbox"}, ["b"]),
        ({"status": "done"}, ["b", "c"]),
        ({"status": NoteStatus.DONE, "topic": "inbox"}, ["c"]),
        ({"count": 7}, ["c"]),
        ({"flag": True}, ["a"]),
        ({"flag": False}, ["b", "c"]),
        ({"owner": "00000000-0000-0000-0000-00000000000a"}, ["a"]),
        ({"owner": None}, ["b", "c"]),
        ({"due": T0.astimezone(timezone(timedelta(hours=2)))}, ["b"]),
        ({"ref": "r"}, []),
    ],
)
async def test_records_are_listed_by_their_indexed_fields(
    db: DatabasePlugin, where: dict[str, object], wanted: list[str]
) -> None:
    await stored(
        db,
        note("inbox", "a", flag=True, owner=UUID(int=10), created_at=T0 + timedelta(seconds=3)),
        note("outbox", "b", status="done", due=T0, created_at=T0 + timedelta(seconds=2)),
        note("inbox", "c", status="done", count=7, created_at=T0 + timedelta(seconds=1)),
    )

    listed = await db.list_records(Note, RecordQuery(where=where))

    assert [record.slug for record in listed] == wanted


async def test_records_are_listed_since_and_until_when_they_were_made(db: DatabasePlugin) -> None:
    records = await stored(db, *(note(created_at=T0 + timedelta(minutes=n)) for n in range(5)))

    since = await db.list_records(Note, RecordQuery(since=T0 + timedelta(minutes=2)))
    until = await db.list_records(Note, RecordQuery(until=T0 + timedelta(minutes=2)))
    between = await db.list_records(
        Note, RecordQuery(since=T0 + timedelta(minutes=1), until=T0 + timedelta(minutes=3))
    )

    assert since == records[:1:-1]  # (from that time on)
    assert until == records[1::-1]  # (before it)
    assert between == records[2:0:-1]


async def test_records_are_listed_a_page_at_a_time(db: DatabasePlugin) -> None:
    records = await stored(db, *(note(created_at=T0 - timedelta(seconds=n)) for n in range(5)))

    page = await db.list_records(Note, RecordQuery(limit=2, offset=1))
    rest = await db.list_records(Note, RecordQuery(offset=3))

    assert page == records[1:3]
    assert rest == records[3:]


async def test_pruning_deletes_the_records_made_before_a_time(db: DatabasePlugin) -> None:
    records = await stored(db, *(note(created_at=T0 + timedelta(days=n)) for n in range(4)))
    tally = await db.upsert_record(Tally(name="t", created_at=T0))

    pruned = await db.prune_records(Note, T0 + timedelta(days=2))

    assert pruned == 2
    assert await db.list_records(Note, RecordQuery()) == records[:1:-1]
    assert await db.list_records(Tally, RecordQuery()) == [tally]
    assert await db.prune_records(Note, T0) == 0


# ---------------------------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------------------------


async def test_a_free_lease_is_claimed(db: DatabasePlugin) -> None:
    lease = await db.claim_lease("automations", "udl-1", LONG, now=T0)

    assert lease is not None
    assert (lease.name, lease.holder, lease.expires_at) == ("automations", "udl-1", T0 + LONG)


async def test_a_held_lease_is_not_claimed_by_another(
    db: DatabasePlugin, other: DatabasePlugin
) -> None:
    await db.claim_lease("automations", "udl-1", LONG, now=T0)

    assert await other.claim_lease("automations", "udl-2", LONG, now=T0 + MINUTE) is None
    assert await db.claim_lease("other work", "udl-2", LONG, now=T0) is not None


async def test_a_lease_is_renewed_by_its_holder(db: DatabasePlugin, other: DatabasePlugin) -> None:
    await db.claim_lease("automations", "udl-1", LONG, now=T0)

    renewed = await other.claim_lease("automations", "udl-1", LONG, now=T0 + MINUTE)

    assert renewed is not None
    assert (renewed.holder, renewed.expires_at) == ("udl-1", T0 + MINUTE + LONG)


async def test_a_lease_that_expired_is_taken_over(
    db: DatabasePlugin, other: DatabasePlugin
) -> None:
    await db.claim_lease("automations", "udl-1", LONG, now=T0)

    taken = await other.claim_lease("automations", "udl-2", LONG, now=T0 + LONG)

    assert taken is not None
    assert taken.holder == "udl-2"
    assert await db.claim_lease("automations", "udl-1", LONG, now=T0 + LONG) is None


async def test_a_released_lease_is_free_at_once(db: DatabasePlugin, other: DatabasePlugin) -> None:
    await db.claim_lease("automations", "udl-1", LONG, now=T0)

    assert await other.release_lease("automations", "udl-2") is False  # (not theirs)
    assert await db.release_lease("automations", "udl-1") is True
    taken = await other.claim_lease("automations", "udl-2", LONG, now=T0 + MINUTE)

    assert taken is not None
    assert taken.holder == "udl-2"
    assert await db.release_lease("automations", "udl-1") is False


async def test_a_lease_is_claimed_by_one_of_many_at_once(
    db: DatabasePlugin, other: DatabasePlugin
) -> None:
    claims = await asyncio.gather(
        *(
            (db if n % 2 else other).claim_lease("automations", f"udl-{n}", LONG, now=T0)
            for n in range(10)
        )
    )

    holders = {claim.holder for claim in claims if claim is not None}
    assert len(holders) == 1


async def test_leases_are_records_of_their_own_kind(db: DatabasePlugin) -> None:
    lease = await db.claim_lease("automations", "udl-1", LONG, now=T0)

    assert await db.list_records(Lease, RecordQuery()) == [lease]
    assert await db.list_records(Note, RecordQuery()) == []


# ---------------------------------------------------------------------------------------------
# Every kind
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", record_kinds(), ids=lambda kind: kind.kind)
async def test_every_kind_round_trips_its_example(db: DatabasePlugin, kind: type[Record]) -> None:
    example = kind.example()

    assert await db.upsert_record(example) == example
    assert await db.get_record(kind, example.id) == example
    assert await db.list_records(kind, RecordQuery()) == [example]


# ---------------------------------------------------------------------------------------------
# The database itself
# ---------------------------------------------------------------------------------------------


async def test_a_kind_defined_after_connecting_is_stored_too(db: DatabasePlugin) -> None:
    fresh = f"test_later_{uuid4().hex}"  # (one no database has seen)

    class Later(Record):  # (e.g. a plugin's, loaded after udl connected)
        kind = fresh
        indexed = ("name",)
        unique = (("name",),)

        name: str

        @classmethod
        def example(cls) -> Later:
            return cls(name="example")

    record = await db.upsert_record(Later(name="late"))

    assert await db.list_records(Later, RecordQuery(where={"name": "late"})) == [record]
    with pytest.raises(DuplicateKey):
        await db.upsert_record(Later(name="late"))


async def test_migrate_keeps_records(db: DatabasePlugin) -> None:
    records = await stored(db, note(created_at=T0), note(created_at=T0 + timedelta(seconds=1)))

    await db.migrate()
    await db.migrate()

    assert await db.list_records(Note, RecordQuery()) == records[::-1]
