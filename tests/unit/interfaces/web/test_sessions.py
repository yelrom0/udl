"""
------------------
- test_sessions.py -
------------------

The session store (udl.interfaces.web.sessions): tokens that a browser keeps, that expire after
[web] session_hours, and that signing out drops — and the sign-in attempts one client may make.
"""

# System Imports
from datetime import UTC, datetime, timedelta

# Local Imports
from udl.interfaces.web.sessions import Attempts, SessionStore


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 19, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


def test_a_session_lives_for_its_hours() -> None:
    clock = Clock()
    store = SessionStore(timedelta(hours=2), now=clock)
    token = store.sign_in()

    assert store.is_live(token)
    clock.advance(timedelta(hours=1, minutes=59))
    assert store.is_live(token)
    clock.advance(timedelta(minutes=2))
    assert not store.is_live(token)


def test_signing_out_drops_a_session() -> None:
    store = SessionStore(timedelta(hours=2))
    token = store.sign_in()

    store.sign_out(token)

    assert not store.is_live(token)


def test_a_token_is_a_256_bit_random_url_safe_string() -> None:
    store = SessionStore(timedelta(hours=2))
    tokens = {store.sign_in() for _ in range(50)}

    assert len(tokens) == 50
    assert all(len(token) >= 43 for token in tokens)
    assert all(
        set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        for token in tokens
    )


def test_an_unknown_or_missing_token_is_not_live() -> None:
    store = SessionStore(timedelta(hours=2))

    assert not store.is_live(None)
    assert not store.is_live("")
    assert not store.is_live("not-a-token")


def test_an_expired_session_is_forgotten() -> None:
    clock = Clock()
    store = SessionStore(timedelta(hours=1), now=clock)
    token = store.sign_in()

    clock.advance(timedelta(hours=2))
    assert not store.is_live(token)
    # (It is not kept around: a long-running server does not grow with every sign-in.)
    assert store.live == 0


def test_signing_out_an_unknown_token_is_harmless() -> None:
    store = SessionStore(timedelta(hours=1))

    store.sign_out("nothing")


def test_ten_attempts_a_minute_per_client() -> None:
    clock = Clock()
    attempts = Attempts(limit=10, window=timedelta(minutes=1), now=clock)

    for _ in range(10):
        assert attempts.allowed("1.2.3.4")
        attempts.record("1.2.3.4")

    assert not attempts.allowed("1.2.3.4")
    # (Another client has its own ten.)
    assert attempts.allowed("5.6.7.8")
    clock.advance(timedelta(minutes=1, seconds=1))
    assert attempts.allowed("1.2.3.4")
