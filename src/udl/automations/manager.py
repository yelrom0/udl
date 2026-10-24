"""
--------------
- manager.py -
--------------

Automations: what the automations' operations do (UDLService's list_events, set_subscription,
run_action... call it): events and their types, subscriptions, actions, runs and the runtime's
status, on a service's databases, bus and runner.

Saving checks what is saved: a subscription against its event types and actions (each action's
options rendered with each type's example), an action against the subscriptions that run it.
An action's secrets (its kind's secret options: a webhook's secret headers) go to the secret
store, under actions/<its id>, and the record keeps where (`{"secret": "<ref>"}`): that is also
what is shown, and what may be given back to keep a secret as it is.
"""

# System Imports
import fnmatch
import os
import typing
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

# Package Imports
from pydantic import BaseModel, SecretStr

# Local Imports
from udl.automations.bus import NewEvent
from udl.automations.events import CUSTOM, EventRecord, EventType, EventTypeList
from udl.automations.filters import Condition, Filter, Op, check, failing, shorthand
from udl.automations.filters import matches as matches_filter
from udl.automations.models import (
    ActionInfo,
    ActionList,
    ActionRequest,
    EmitEventRequest,
    EventDetail,
    EventInfo,
    EventList,
    EventMatch,
    EventRequest,
    ListActionsRequest,
    ListEventsRequest,
    ListEventTypesRequest,
    ListRunsRequest,
    ListSchedulesRequest,
    ListScriptsRequest,
    ListSubscriptionsRequest,
    PreviewScheduleRequest,
    ReplayEventRequest,
    RunActionRequest,
    RunActionResult,
    RunDetail,
    RunInfo,
    RunList,
    RunRequest,
    ScheduleInfo,
    ScheduleList,
    SchedulePreview,
    ScheduleRequest,
    ScheduleTime,
    ScriptInfo,
    ScriptList,
    SetActionRequest,
    SetScheduleRequest,
    SetSubscriptionRequest,
    StatusRequest,
    SubscriptionInfo,
    SubscriptionList,
    SubscriptionRequest,
    SubscriptionTestReport,
    SubscriptionTestRequest,
)
from udl.automations.runs import ActionRun
from udl.automations.runtime import RuntimeStatus, runtime_status
from udl.automations.scheduler import FIRED
from udl.automations.schedules import (
    EverySpec,
    Schedule,
    next_times,
    parse_spec,
    schedule_zone,
    spec_text,
)
from udl.automations.subscriptions import (
    SCHEDULE_PREFIX,
    Subscription,
    check_subscription,
    types_of,
)
from udl.automations.templates import Templates
from udl.core.models import Deleted
from udl.core.operations import current_caller
from udl.db.models import JsonObject
from udl.db.records import Record, RecordQuery
from udl.errors import ConfigError, DoesNotExist, InUse, NotAllowed, UDLError
from udl.plugins.actions.base import Action, ActionPlugin, template_context
from udl.plugins.registry import ACTIONS
from udl.plugins.requirements import unmet
from udl.plugins.secrets.base import KeptSecret, SecretField, SecretRef

if TYPE_CHECKING:
    from udl.core.service import UDLService

_SCAN: Final = 200  # records read at a time, when a list is filtered here
_TRIED_AT_MOST: Final = 2000  # events read to find a subscription's latest, at most
_HIDDEN: Final = "***"
UPCOMING: Final = 5
"""How many of a schedule's next times get_schedule says."""
# (A schedule's fields that its request gives as they are.)
_SCHEDULE_FIELDS: Final = frozenset(
    {"name", "description", "timezone", "data", "misfire", "misfire_grace", "jitter", "enabled"}
)


class Automations:
    """The automations' operations, on `service`."""

    def __init__(self, service: UDLService) -> None:
        self.service = service

    # -- events --------------------------------------------------------------------------------

    def event_types(self, request: ListEventTypesRequest) -> EventTypeList:
        return self.service.event_types.info(request.match)

    async def list_events(self, request: ListEventsRequest) -> EventList:
        where: dict[str, Any] = {}
        exact = not any(mark in request.type for mark in "*?[")
        if exact:
            where["type"] = request.type
        for name, value in (
            ("source", request.source),
            ("account_id", request.account_id),
            ("status", request.status),
        ):
            if value is not None:
                where[name] = value
        found = await self._listed(
            EventRecord,
            where,
            lambda record: exact or fnmatch.fnmatchcase(record.type, request.type),
            limit=request.limit,
            offset=request.offset,
            since=request.since,
            until=request.until,
        )
        return EventList(events=[EventInfo.of(record) for record in found])

    async def get_event(self, request: EventRequest) -> EventDetail:
        event = await self._event(request.id)
        runs = await self.service.db.list_records(
            ActionRun, RecordQuery(where={"event_id": event.id})
        )
        return EventDetail(event=EventInfo.of(event), runs=[RunInfo.of(run) for run in runs])

    async def emit_event(self, request: EmitEventRequest) -> EventInfo:
        caller = current_caller.get()
        stored = await self.service.bus.publish(
            NewEvent(
                type=request.type,
                data=request.data,
                source=caller.value if caller is not None else "udl",
                dedupe_key=request.dedupe_key,
            )
        )
        if stored is None:  # (seen before: the event is the one stored then)
            [stored] = await self.service.db.list_records(
                EventRecord,
                RecordQuery(where={"type": request.type, "dedupe_key": request.dedupe_key}),
            )
        return EventInfo.of(stored)

    async def replay_event(self, request: ReplayEventRequest) -> RunList:
        event = await self._event(request.id)
        if request.subscription is not None:
            await self._subscription(request.subscription)
        bus = self.service.bus
        bus.subscriptions_changed()
        runs = await bus.dispatch(event, replay=True, subscription=request.subscription)
        self.service.runner.wake()
        return RunList(runs=[RunInfo.of(run) for run in runs])

    async def _event(self, event_id: UUID) -> EventRecord:
        found = await self.service.db.get_record(EventRecord, event_id)
        if found is None:
            raise DoesNotExist(
                f"No event {event_id}",
                hint="`udl events list` lists them (events older than "
                "[automations] event_retention_days are pruned).",
            )
        return found

    # -- subscriptions -------------------------------------------------------------------------

    async def list_subscriptions(self, request: ListSubscriptionsRequest) -> SubscriptionList:
        where = {} if request.enabled is None else {"enabled": request.enabled}
        found = await self.service.db.list_records(Subscription, RecordQuery(where=where))
        return SubscriptionList(
            subscriptions=[
                SubscriptionInfo.of(subscription)
                for subscription in sorted(found, key=lambda subscription: subscription.name)
            ]
        )

    async def get_subscription(self, request: SubscriptionRequest) -> SubscriptionInfo:
        return SubscriptionInfo.of(await self._subscription(request.name))

    async def set_subscription(self, request: SetSubscriptionRequest) -> SubscriptionInfo:
        existing = await self._named(Subscription, request.name)
        subscription = Subscription.model_validate(
            {**request.model_dump(), "id": existing.id if existing else uuid4()}
        )
        actions = await self._actions()
        types = self.service.event_types
        checks = self.service.integrations.source_filter_checks()
        check_subscription(subscription, types, actions, source_filters=checks)
        for name in subscription.actions:
            await self._check_action(actions[name], types_of(subscription, types))
        stored = await self.service.db.upsert_record(subscription)
        self._subscriptions_changed()
        return SubscriptionInfo.of(stored)

    async def try_subscription(self, request: SubscriptionTestRequest) -> SubscriptionTestReport:
        saved = await self._subscription(request.name) if request.name is not None else None
        events = request.events or (saved.events if saved is not None else None)
        if not events:
            raise ConfigError(
                "A subscription to try names its event types (or a saved subscription)",
                hint="Give a subscription's name, or the event types (--events) to try.",
            )
        accounts = request.accounts
        tree: Filter | None = request.filter
        if saved is not None:
            accounts = accounts if accounts is not None else saved.accounts
            tree = tree if tree is not None else saved.filter
        types = self.service.event_types
        problems: list[str] = []
        if saved is not None and not saved.enabled:
            problems.append("It is disabled: it matches nothing until it is enabled.")
        for glob in events:
            if not types.matching(glob) and not types.may_be_custom(glob):
                problems.append(f"No event type is {glob!r}.")
        could_get = [found for glob in events for found in types.matching(glob)]
        if any(types.may_be_custom(glob) for glob in events):
            could_get.append(CUSTOM)
        if tree is not None:
            try:
                check(tree, could_get)
            except UDLError as e:
                problems.append(e.message)
        tried = (
            [await self._event(request.event)]
            if request.event is not None
            else await self._latest(events, request.recent)
        )
        names = await self.service.accounts.names()

        def match(event: EventRecord) -> EventMatch:
            failed: str | None = None
            account = names.get(event.account_id) if event.account_id is not None else None
            if not any(fnmatch.fnmatchcase(event.type, glob) for glob in events):
                failed = "its type"
            elif accounts is not None and account not in accounts:
                failed = "its account"
            elif tree is not None and not matches_filter(tree, event.data):
                failed = failing(tree, event.data)
            return EventMatch(event=EventInfo.of(event), matched=failed is None, failed=failed)

        return SubscriptionTestReport(
            filter=None if tree is None else shorthand(tree),
            tree=tree,
            problems=problems,
            events=[match(event) for event in tried],
        )

    async def _latest(self, globs: list[str], count: int) -> list[EventRecord]:
        """The latest `count` events whose type one of `globs` matches."""
        return await self._listed(
            EventRecord,
            {},
            lambda record: any(fnmatch.fnmatchcase(record.type, glob) for glob in globs),
            limit=count,
            most=_TRIED_AT_MOST,
        )

    async def delete_subscription(self, request: SubscriptionRequest) -> Deleted:
        if request.name.startswith(SCHEDULE_PREFIX):
            schedule = request.name.removeprefix(SCHEDULE_PREFIX)
            raise NotAllowed(
                f"{request.name!r} is the {schedule} schedule's: it runs that schedule's actions",
                hint=f"Change the schedule's actions (`udl schedules set {schedule} ... --action "
                f"...`), or delete the schedule (`udl schedules rm {schedule}`).",
            )
        found = await self._named(Subscription, request.name)
        if found is None:
            return Deleted(deleted=False)
        deleted = await self.service.db.delete_record(Subscription, found.id)
        self._subscriptions_changed()
        return Deleted(deleted=deleted)

    async def _subscription(self, name: str) -> Subscription:
        found = await self._named(Subscription, name)
        if found is None:
            raise DoesNotExist(
                f"No subscription called {name!r}", hint="`udl subs list` lists them."
            )
        return found

    # -- actions -------------------------------------------------------------------------------

    async def list_actions(self, request: ListActionsRequest) -> ActionList:
        where = {} if request.plugin is None else {"plugin": request.plugin}
        found = await self.service.db.list_records(Action, RecordQuery(where=where))
        users = await self._users()
        return ActionList(
            actions=[
                self._info(action, users.get(action.name, []))
                for action in sorted(found, key=lambda action: action.name)
            ]
        )

    async def get_action(self, request: ActionRequest) -> ActionInfo:
        action = await self._action(request.name)
        return self._info(action, (await self._users()).get(action.name, []))

    async def set_action(self, request: SetActionRequest) -> ActionInfo:
        plugin = self._plugin(request.plugin)  # (raises PluginError)
        existing = await self._named(Action, request.name)
        action = Action.model_validate(
            {**request.model_dump(), "id": existing.id if existing else uuid4()}
        )
        users = (await self._users()).get(action.name, [])
        subscriptions = {sub.name: sub for sub in await self._all(Subscription)}
        types = {
            event_type.name: event_type
            for name in users
            for event_type in types_of(subscriptions[name], self.service.event_types)
        }
        for event_type in list(types.values()) or [None]:
            await plugin.check(action.options, event_type)
        options = await self._keep_secrets(action, plugin.options_model)
        stored = await self.service.db.upsert_record(action.model_copy(update={"options": options}))
        self._subscriptions_changed()
        return self._info(stored, users)

    async def run_action(self, request: RunActionRequest) -> RunActionResult:
        action = await self._action(request.name)
        event = await self._event(request.event) if request.event is not None else None
        if not request.dry_run:
            run = await self.service.runner.run_now(action, event)
            return RunActionResult(run=RunDetail.of(run), options=None)
        plugin = self._plugin(action.plugin)
        context = template_context(
            event.event() if event is not None else None,
            run={"id": None, "action": action.name, "subscription": None, "attempt": 1},
            now=self.service.clock.now(),
            account=await self.service.accounts.name_of(event.account_id if event else None),
        )
        rendered = Templates().render_value(action.options, context)
        options = plugin.options(rendered if isinstance(rendered, dict) else {})
        return RunActionResult(run=None, options=_shown(options))

    async def delete_action(self, request: ActionRequest) -> Deleted:
        found = await self._named(Action, request.name)
        if found is None:
            return Deleted(deleted=False)
        users = (await self._users()).get(found.name, [])
        if users:
            raise InUse(
                f"The {found.name} action is run by {', '.join(users)}",
                hint="Take it out of those subscriptions (or delete them) first.",
            )
        deleted = await self.service.db.delete_record(Action, found.id)
        await self.service.secrets.delete_prefix(_secrets_of(found))
        self._subscriptions_changed()
        return Deleted(deleted=deleted)

    def list_scripts(self, request: ListScriptsRequest) -> ScriptList:
        folder = self.service.settings.automations.scripts_dir.expanduser()
        if not folder.is_dir():
            return ScriptList(dir=folder, exists=False, scripts=[])
        scripts: list[ScriptInfo] = []
        for path in sorted(folder.rglob("*")):
            name = path.relative_to(folder)
            if not path.is_file() or any(part.startswith(".") for part in name.parts):
                continue
            found = path.stat()
            scripts.append(
                ScriptInfo(
                    name=str(name),
                    executable=os.access(path, os.X_OK),
                    size=found.st_size,
                    modified=datetime.fromtimestamp(found.st_mtime, UTC),
                )
            )
        return ScriptList(dir=folder, exists=True, scripts=scripts)

    async def _action(self, name: str) -> Action:
        found = await self._named(Action, name)
        if found is None:
            raise DoesNotExist(f"No action called {name!r}", hint="`udl actions list` lists them.")
        return found

    async def _actions(self) -> dict[str, Action]:
        return {action.name: action for action in await self._all(Action)}

    async def _users(self) -> dict[str, list[str]]:
        """The subscriptions that run each action, by the action's name."""
        users: dict[str, list[str]] = {}
        for subscription in sorted(await self._all(Subscription), key=lambda sub: sub.name):
            for name in subscription.actions:
                users.setdefault(name, []).append(subscription.name)
        return users

    def _plugin(self, kind: str) -> ActionPlugin[Any]:
        """The action plugin called `kind`, for the service. Raises PluginError."""
        return self.service.registry.get(ACTIONS, kind)(self.service)

    async def _check_action(self, action: Action, types: Iterable[EventType[Any]]) -> None:
        plugin = self._plugin(action.plugin)
        for event_type in list(types) or [None]:
            await plugin.check(action.options, event_type)

    def _info(self, action: Action, used_by: list[str]) -> ActionInfo:
        warnings: list[str] = []
        schema: JsonObject = {}
        try:
            plugin = self.service.registry.get(ACTIONS, action.plugin)
        except UDLError as e:
            warnings.append(e.message)
        else:
            schema = typing.cast(JsonObject, plugin.options_model.model_json_schema())
            for requirement in unmet(plugin.requires):
                warnings.append(
                    f"The {action.plugin} kind needs {requirement.name}"
                    + (f": {requirement.hint}" if requirement.hint else "")
                )
        if not action.enabled:
            warnings.append("It is disabled: its runs are skipped.")
        return ActionInfo.of(action, options_schema=schema, used_by=used_by, warnings=warnings)

    async def _keep_secrets(self, action: Action, model: type[BaseModel]) -> JsonObject:
        """An action's options with each secret in them kept in the secret store (under
        actions/<its id>), and where it is kept in its place."""
        prefix = _secrets_of(action)
        store = self.service.secrets
        options = dict(action.options)
        secrets: dict[SecretRef, SecretStr] = {}
        for name, many in _secret_fields(model).items():
            given = options.get(name)
            if given is None:
                continue
            if many and isinstance(given, dict):
                options[name] = {
                    key: await self._secret(value, SecretRef.of(prefix, name, key), prefix, secrets)
                    for key, value in given.items()
                }
            elif not many:
                options[name] = await self._secret(
                    given, SecretRef.of(prefix, name), prefix, secrets
                )
        await store.delete_prefix(prefix)
        for ref, value in secrets.items():
            await store.set(ref, value)
        return options

    async def _secret(
        self, given: Any, ref: SecretRef, prefix: SecretRef, secrets: dict[SecretRef, SecretStr]
    ) -> Any:
        """A secret option's value as it is kept (its reference), the secret put in `secrets`
        (to keep): a text is a new secret; a kept one's reference keeps it as it is."""
        if isinstance(given, str):
            secrets[ref] = SecretStr(given)
            return KeptSecret(secret=ref).model_dump(mode="json")
        kept = KeptSecret.model_validate(given)  # (the kind's check let nothing else by)
        if not kept.secret.within(prefix):
            raise NotAllowed(
                f"{kept.secret} is not this action's secret",
                hint="Give the secret itself, or this action's own reference to it.",
            )
        value = await self.service.secrets.get(kept.secret)
        if value is None:
            raise ConfigError(
                f"The secret store has no {kept.secret}", hint="Give the secret itself again."
            )
        secrets[kept.secret] = value
        return given

    # -- runs ----------------------------------------------------------------------------------

    async def list_runs(self, request: ListRunsRequest) -> RunList:
        where: dict[str, Any] = {}
        for name, value in (
            ("status", request.status),
            ("action", request.action),
            ("subscription", request.subscription),
            ("event_id", request.event),
        ):
            if value is not None:
                where[name] = value
        found = await self.service.db.list_records(
            ActionRun,
            RecordQuery(
                where=where, since=request.since, limit=request.limit, offset=request.offset
            ),
        )
        return RunList(runs=[RunInfo.of(run) for run in found])

    async def get_run(self, request: RunRequest) -> RunDetail:
        found = await self.service.db.get_record(ActionRun, request.id)
        if found is None:
            raise DoesNotExist(f"No run {request.id}", hint="`udl runs list` lists them.")
        return RunDetail.of(found)

    async def cancel_run(self, request: RunRequest) -> RunInfo:
        return RunInfo.of(await self.service.runner.cancel(request.id))

    async def status(self, request: StatusRequest) -> RuntimeStatus:
        return await runtime_status(self.service)

    # -- schedules -----------------------------------------------------------------------------

    async def list_schedules(self, request: ListSchedulesRequest) -> ScheduleList:
        where = {} if request.enabled is None else {"enabled": request.enabled}
        found = await self.service.db.list_records(Schedule, RecordQuery(where=where))
        linked = {
            subscription.name: subscription
            for subscription in await self._all(Subscription)
            if subscription.name.startswith(SCHEDULE_PREFIX)
        }
        return ScheduleList(
            schedules=[
                self._info_of(schedule, linked.get(SCHEDULE_PREFIX + schedule.name))
                for schedule in sorted(found, key=lambda schedule: schedule.name)
            ]
        )

    async def run_schedule(self, request: ScheduleRequest) -> EventInfo:
        schedule = await self._schedule(request.name)
        caller = current_caller.get()
        fired = await self.service.scheduler.run_now(
            schedule, source=caller.value if caller is not None else "udl"
        )
        return EventInfo.of(fired)

    def preview_schedule(self, request: PreviewScheduleRequest) -> SchedulePreview:
        spec = parse_spec(request.spec)
        zone = schedule_zone(request.timezone, self.service.settings.automations.timezone)
        after = request.after or self.service.clock.now()
        return SchedulePreview(
            spec=spec,
            spec_text=spec_text(spec),
            zone=str(zone),
            times=[
                ScheduleTime(local=time.astimezone(zone), utc=time)
                for time in next_times(spec, zone, after, request.count)
            ],
        )

    async def get_schedule(self, request: ScheduleRequest) -> ScheduleInfo:
        return await self._schedule_info(await self._schedule(request.name))

    async def set_schedule(self, request: SetScheduleRequest) -> ScheduleInfo:
        existing = await self._named(Schedule, request.name)
        spec = request.spec
        if (  # (an interval saved again keeps counting from where it did)
            isinstance(spec, EverySpec)
            and spec.start is None
            and existing is not None
            and isinstance(existing.spec, EverySpec)
            and existing.spec.every == spec.every
        ):
            spec = existing.spec
        schedule = Schedule.model_validate(
            {
                **request.model_dump(include=set(_SCHEDULE_FIELDS)),
                "id": existing.id if existing is not None else uuid4(),
                "spec": spec,
                "last_run_at": existing.last_run_at if existing is not None else None,
            }
        )
        planned = schedule.planned(self.service.clock.now(), self._zone(schedule))
        if schedule.enabled and not planned.enabled:
            raise ConfigError(
                f"{spec_text(spec)} has gone by: an at schedule fires once, at a time to come",
                hint="Give a time to come, or save it disabled (--no-enabled).",
            )
        linked = await self._linked(planned, request.actions or [])
        stored = await self.service.db.upsert_record(planned)
        if request.actions is not None:
            await self._keep_linked(stored.name, linked)
        self.service.scheduler.changed()
        return await self._schedule_info(stored)

    async def delete_schedule(self, request: ScheduleRequest) -> Deleted:
        found = await self._named(Schedule, request.name)
        if found is None:
            return Deleted(deleted=False)
        deleted = await self.service.db.delete_record(Schedule, found.id)
        await self._keep_linked(found.name, None)
        self.service.scheduler.changed()
        return Deleted(deleted=deleted)

    async def _schedule(self, name: str) -> Schedule:
        found = await self._named(Schedule, name)
        if found is None:
            raise DoesNotExist(
                f"No schedule called {name!r}", hint="`udl schedules list` lists them."
            )
        return found

    def _zone(self, schedule: Schedule) -> ZoneInfo:
        return schedule.zone(self.service.settings.automations.timezone)

    async def _schedule_info(self, schedule: Schedule) -> ScheduleInfo:
        linked = await self._named(Subscription, SCHEDULE_PREFIX + schedule.name)
        return self._info_of(schedule, linked)

    def _info_of(self, schedule: Schedule, linked: Subscription | None) -> ScheduleInfo:
        zone = self._zone(schedule)
        return ScheduleInfo.of(
            schedule,
            zone=str(zone),
            next_times=schedule.upcoming(zone, UPCOMING),
            subscription=linked,
        )

    async def _linked(self, schedule: Schedule, actions: list[str]) -> Subscription | None:
        """The subscription that runs `actions` when `schedule` fires (schedule:<its name>),
        checked as set_subscription checks one; None for no actions."""
        if not actions:
            return None
        name = SCHEDULE_PREFIX + schedule.name
        existing = await self._named(Subscription, name)
        subscription = Subscription(
            id=existing.id if existing is not None else uuid4(),
            name=name,
            description=f"Runs the {schedule.name} schedule's actions (`udl schedules set`).",
            events=[FIRED],
            filter=Condition(field="schedule", op=Op.EQ, value=schedule.name),
            actions=actions,
        )
        known = await self._actions()
        types = self.service.event_types
        check_subscription(subscription, types, known, for_schedule=True)
        for action in subscription.actions:
            await self._check_action(known[action], types_of(subscription, types))
        return subscription

    async def _keep_linked(self, schedule: str, linked: Subscription | None) -> None:
        """Save `linked` as the schedule's subscription, or (None) delete the one it has."""
        if linked is not None:
            await self.service.db.upsert_record(linked)
        else:
            found = await self._named(Subscription, SCHEDULE_PREFIX + schedule)
            if found is not None:
                await self.service.db.delete_record(Subscription, found.id)
        self._subscriptions_changed()

    def _subscriptions_changed(self) -> None:
        """Tell the bus and the watchers the subscriptions (or actions) changed."""
        self.service.bus.subscriptions_changed()
        self.service.watchers.changed()

    # -- looking records up --------------------------------------------------------------------

    async def _named[R: Record](self, kind: type[R], name: str) -> R | None:
        found = await self.service.db.list_records(kind, RecordQuery(where={"name": name}))
        return found[0] if found else None

    async def _all[R: Record](self, kind: type[R]) -> list[R]:
        return await self.service.db.list_records(kind, RecordQuery())

    async def _listed[R: Record](
        self,
        kind: type[R],
        where: dict[str, Any],
        wanted: Callable[[R], bool],
        *,
        limit: int,
        offset: int = 0,
        since: datetime | None = None,
        until: datetime | None = None,
        most: int | None = None,
    ) -> list[R]:
        """The records of `kind` that `where` and `wanted` pick, newest first: a page of them
        (`limit` after `offset`), reading at most `most` records."""
        found: list[R] = []
        read = 0
        while len(found) < offset + limit and (most is None or read < most):
            page = await self.service.db.list_records(
                kind, RecordQuery(where=where, since=since, until=until, limit=_SCAN, offset=read)
            )
            found += [record for record in page if wanted(record)]
            read += len(page)
            if len(page) < _SCAN:
                break
        return found[offset : offset + limit]


def _secrets_of(action: Action) -> SecretRef:
    return SecretRef.of("actions", action.id)


def _secret_fields(model: type[BaseModel]) -> dict[str, bool]:
    """The fields of an action kind's options that are secrets: each one's name, and whether it
    is a dict of them (a webhook's secret headers)."""
    found: dict[str, bool] = {}
    for name, info in model.model_fields.items():
        annotation = _plain(info.annotation)
        if annotation is SecretField:
            found[name] = False
        elif (
            typing.get_origin(annotation) is dict
            and _plain(typing.get_args(annotation)[1]) is SecretField
        ):
            found[name] = True
    return found


def _plain(annotation: Any) -> Any:
    args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
    if typing.get_origin(annotation) in (typing.Union, type(int | None)) and len(args) == 1:
        return args[0]
    return annotation


def _shown(options: BaseModel) -> JsonObject:
    """Rendered options, as JSON, with their secrets hidden."""
    return _hide(typing.cast(JsonObject, options.model_dump(mode="json")), options)


def _hide(dumped: JsonObject, options: BaseModel) -> JsonObject:
    for name, many in _secret_fields(type(options)).items():
        value = dumped.get(name)
        if many and isinstance(value, dict):
            dumped[name] = dict.fromkeys(value, _HIDDEN)
        elif not many and value is not None:
            dumped[name] = _HIDDEN
    return dumped
