"""
------------------
- automations.py -
------------------

udl events, udl subs, udl actions, udl runs, udl schedules and udl status: the automations'
commands, and udl accounts' (but `add`), made from their operations
(udl.interfaces.adapters.add_commands), shown by these renderers
"""

# System Imports
from collections.abc import Callable
from typing import Final

# Local Imports
from udl.interfaces.cli import render

RENDERERS: Final[dict[str, Callable[..., None]]] = {
    "list_event_types": render.event_types,
    "list_events": render.events,
    "get_event": render.event_detail,
    "emit_event": render.event,
    "replay_event": render.runs,
    "list_subscriptions": render.subscriptions,
    "get_subscription": render.subscription,
    "set_subscription": render.subscription,
    "test_subscription": render.subscription_test,
    "delete_subscription": render.deleted,
    "list_actions": render.actions,
    "get_action": render.action,
    "set_action": render.action,
    "run_action": render.run_result,
    "delete_action": render.deleted,
    "list_scripts": render.scripts,
    "list_runs": render.runs,
    "get_run": render.run,
    "cancel_run": render.run,
    "get_status": render.status,
    "list_schedules": render.schedules,
    "get_schedule": render.schedule,
    "set_schedule": render.schedule,
    "run_schedule": render.event,
    "preview_schedule": render.schedule_preview,
    "delete_schedule": render.deleted,
    "list_accounts": render.accounts,
    "get_account": render.account,
    "set_account": render.account,
    "check_account": render.account_health,
    "disconnect_account": render.disconnected,
}
"""How each of the automations' commands shows its response, by operation."""

HELPS: Final[dict[str, str]] = {
    "events": "Events: what happened (a download ended, a run ended, an event of your own), "
    "the kinds of event, and emitting your own.",
    "subs": "Subscriptions: when an event like this happens, run these actions.",
    "actions": "Actions: named things to run (a script, an operation, a webhook, an event, a "
    "notification) when a subscription's event comes.",
    "runs": "Runs: each action that ran (or will) for an event.",
    "schedules": "Schedules: cron jobs. When each fires schedule.fired (a cron expression, an "
    "interval or a time), and the actions it runs then.",
}
"""The automations' command groups' help."""
