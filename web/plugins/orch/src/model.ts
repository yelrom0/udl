/**
 * What the plugin reads of udl's answers: the orchestrator's own shapes, as the operations send
 * them (a JSON object each), narrowed here so a page never reads a field blind. Only what the
 * plugin draws is here; the rest stays `JsonValue` until a page needs it.
 */

import type { JsonValue } from "@udl/ui-sdk";
import { asArray, asDecimal, asFlag, asNumber, asObject, asText } from "./json";

/** The pull open on a task: its number, and where it is. */
export interface OpenPull {
	readonly number: number;
	readonly url: string;
}

/** A task's size (26.8), and what it is worth. */
export type Size = "S" | "M" | "L" | "XL";

/** The points a size is worth (26.8's own table). */
export const POINTS: Record<Size, number> = { S: 1, M: 3, L: 5, XL: 8 };

/** A size, or null when it is not one of the four. */
export function asSize(value: JsonValue | undefined): Size | null {
	return value === "S" || value === "M" || value === "L" || value === "XL" ? value : null;
}

/** What is known of a pull (`Link.state`): its checks and the review bot's word. */
export interface PullState {
	readonly checks?: string;
	readonly review?: string;
}

/** One task in a merge list (28.3). */
export interface MergeRow {
	/** The project its task is in (the list it came from: a key is only unique within one). */
	readonly project: string;
	readonly task: string;
	readonly feature: string;
	readonly title: string;
	readonly number: number;
	readonly url: string;
	readonly sha: string;
	readonly state: string;
	readonly blockers: readonly string[];
	readonly merger: string | null;
}

/** A project's merge list: ready, coming, held, and what merged lately. */
export interface MergeList {
	readonly project: string;
	readonly auto_merge: boolean;
	readonly merger: string | null;
	readonly ready: readonly MergeRow[];
	readonly coming: readonly MergeRow[];
	readonly held: readonly MergeRow[];
	readonly done: readonly MergeRow[];
}

/** A question an agent asked (28.1). */
export interface Question {
	readonly key: string;
	readonly project: string;
	readonly title: string;
	readonly text: string;
	readonly asked_by: string;
	readonly to: string;
	readonly state: string;
	readonly task: string | null;
	readonly options: readonly string[];
	readonly blocking: boolean;
	/** What was answered, by whom, and what was done instead when it was closed unanswered. */
	readonly answer: string | null;
	readonly answered_by: string | null;
	readonly outcome: string | null;
	readonly escalated: boolean;
}

/** One thing said on a question's thread (28.1). */
export interface Message {
	readonly text: string;
	readonly author: string;
	readonly at: string;
}

/** A question with its thread and what it names (`orch_get_question`, 28.1). */
export interface QuestionThread {
	readonly question: Question;
	readonly thread: readonly Message[];
	readonly tasks: readonly string[];
	readonly features: readonly string[];
}

/** What only a person can do (28.2). */
export interface Action {
	readonly key: string;
	readonly title: string;
	readonly text: string;
	readonly when: string;
	readonly after: string | null;
	readonly task: string | null;
	readonly assignee: string;
}

/** What an agent is doing, in a line (28.4's inbox strip). */
export interface AgentLine {
	readonly name: string;
	readonly title: string;
	readonly role: string;
	readonly state: string;
	readonly task: string | null;
	readonly line: string;
}

/** The inbox: what waits on a person, in one answer (28.4). */
export interface Inbox {
	readonly project: string;
	readonly merge: MergeList;
	readonly questions: readonly Question[];
	readonly actions: readonly Action[];
	readonly agents: readonly AgentLine[];
	readonly updated_at: string;
}

/** A feature: a part of a project's work (26.2). */
export interface Feature {
	readonly key: string;
	readonly project: string;
	readonly title: string;
	readonly status: string;
}

/** A task: one piece of a feature (26.3, 26.8). */
export interface Task {
	readonly key: string;
	/** The project it is in (a task's key is only unique within one). */
	readonly project: string;
	readonly feature: string;
	readonly title: string;
	readonly state: string;
	readonly assignee: string | null;
	readonly size: Size | null;
	readonly needs: readonly string[];
	readonly spec: string;
	/** Why it is held (built now, merged later), or null. */
	readonly hold: string | null;
	readonly blocked_on: string | null;
	readonly repo: string;
	readonly branch: string;
	readonly worktree: string;
	readonly task_kind: string;
}

/** Every state a task is in (26.3's Concepts table), in the order a person reads them. */
export const STATES: readonly string[] = [
	"open",
	"queued",
	"in_progress",
	"blocked",
	"ready",
	"merged",
	"done",
	"moved",
	"cancelled",
];

/** The states a person may move a task to: every one but the one it is in (a person moves a
 * task anywhere; the table's rules are for the agents). */
export function movesFrom(state: string): readonly string[] {
	return STATES.filter((one) => one !== state);
}

/** A note on a feature or a task (26.5), as an operation sends it. */
export interface Note {
	readonly at: string;
	readonly author: string;
	readonly note_kind: string;
	readonly text: string;
}

/** A typed pointer from a feature or a task to something else (26.4). */
export interface Link {
	readonly link_kind: string;
	readonly url: string;
	readonly title: string;
	readonly state: PullState;
}

/** What a feature's or a project's tasks come to, in points (26.8). */
export interface Totals {
	readonly points: number;
	readonly points_merged: number;
	readonly points_ready: number;
}

/** A feature, with what hangs on it: its spec, its tasks, its links, its notes, its totals. */
export interface FeatureInfo {
	readonly feature: Feature;
	readonly spec: string;
	readonly status: string;
	readonly totals: Totals;
	readonly tasks: readonly Task[];
	readonly links: readonly Link[];
	readonly notes: readonly Note[];
}

/** A task, with what hangs on it: its feature, its links, its notes, its questions, and the
 * tasks that wait on it. */
export interface TaskInfo {
	readonly task: Task;
	readonly feature: Feature | null;
	readonly links: readonly Link[];
	readonly notes: readonly Note[];
	readonly questions: readonly Question[];
	readonly unblocks: readonly string[];
}

/** What a task is expected to take, and what it has taken (27.10). */
export interface Estimate {
	readonly size: Size | null;
	readonly estimate: string | null;
	readonly worked: string;
}

/** One of the review bot's items, as udl reads it out of the bot's comment (27.6). */
export interface ReviewItem {
	/** Its number in the comment (`1`, or empty for a row without one). */
	readonly number: string;
	/** Where it is; anything but `fixed` keeps the pull from being ready. */
	readonly status: string;
	readonly text: string;
}

/** Whether a task's pull may be merged, and what it waits on (27.6). */
export interface Readiness {
	readonly ready: boolean;
	readonly held: boolean;
	readonly blockers: readonly string[];
	readonly pending: readonly string[];
	readonly open_items: readonly ReviewItem[];
	readonly waited: number;
}

/** The session a task is in (27.7), as an agent's own answer names it. */
export interface TaskSession {
	readonly agent: string;
	readonly id: string;
	readonly state: string;
}

/** A task on an agent's queue, as `orch_list_agents` sends it (29.4). */
export interface QueueEntry {
	readonly key: string;
	/** The project its task is in (a key is only unique within one). */
	readonly project: string;
	readonly feature: string;
	readonly title: string;
	readonly state: string;
	readonly position: number | null;
}

/** An agent, as the board shows it (27.1, 29.4): what it is, what it is doing, its queue, and
 * the agents that report to it. */
export interface Agent {
	readonly name: string;
	readonly title: string;
	readonly role: string;
	readonly status: string;
	readonly model: string;
	readonly queue: readonly QueueEntry[];
	readonly reports: readonly string[];
}

/** A task suggested next, with the agent it suits and why (27.4, 29.4). */
export interface Suggestion {
	readonly task: string;
	readonly title: string;
	readonly feature: string;
	readonly agent: string;
	readonly score: number;
	readonly reasons: readonly string[];
	/** The task whose branch it stacks on, when its need is `ready` and not merged yet. */
	readonly stack_on: string | null;
}

/** A feature's points, as `orch_progress` sends them (27.10): its bar, per Q-8. */
export interface FeaturePoints {
	readonly key: string;
	readonly points: number;
	readonly merged: number;
}

/** What a point costs a model, as `orch_progress` sends it (27.10): an agent's column's hours. */
export interface ModelRate {
	readonly model: string;
	readonly hours_per_point: number;
}

/** The board's share of a project's progress (27.10). */
export interface Progress {
	readonly features: readonly FeaturePoints[];
	readonly models: readonly ModelRate[];
}

/** What the board reads: features, tasks, agents, suggestions, the merge list and progress. */
export interface Board {
	readonly project: string;
	readonly features: readonly Feature[];
	readonly tasks: readonly Task[];
	readonly agents: readonly Agent[];
	readonly suggestions: readonly Suggestion[];
	readonly merge: MergeList;
	readonly progress: Progress;
}

/** What a session has used and where it got to, as an agent's own answer carries it (27.9). */
export interface SessionSummary {
	readonly id: string;
	readonly task: string;
	readonly state: string;
	readonly turns: number;
	readonly tokens_in: number;
	readonly tokens_out: number;
	readonly cost: number | null;
	readonly last_text: string;
}

/** One thing a session's runner said, as its transcript holds it (27.9's `AnAgentEvent`). */
export interface Spoken {
	/** The turn it happened in (0: before the first one). */
	readonly turn: number;
	/** When it was stored. */
	readonly at: string;
	/** Which of the runner's events it is (`text_delta`, `tool_started`, `usage`, …). */
	readonly kind: string;
	/** What it said, for the kinds that say something. */
	readonly text: string;
	/** The tool's name, for a tool's events. */
	readonly name: string;
	/** What a tool is doing, in a line. */
	readonly summary: string;
	/** Whether a tool that ended worked (null: it is not a tool's end). */
	readonly ok: boolean | null;
	/** What a turn's usage cost, when the harness said. */
	readonly cost: number | null;
	readonly tokens_in: number;
	readonly tokens_out: number;
	/** How a turn ended, for `turn_ended`. */
	readonly reason: string;
	/** What a failed or a limited runner said (`runner_failed`, `runner_limited`). */
	readonly message: string;
	/** When a limited runner's quota is back, if it said (`runner_limited`). */
	readonly until: string | null;
}

/** The harness's own session: the runner, its id, the folder and the host. */
export interface SessionRef {
	readonly runner: string;
	readonly id: string;
	readonly cwd: string;
	readonly host: string;
}

/** A session with its transcript, as `orch_get_session` answers it. */
export interface Session extends SessionSummary {
	readonly agent: string;
	readonly created_at: string;
	readonly limited_until: string | null;
	readonly ref: SessionRef;
	/** How many events its transcript has (`events` is the page). */
	readonly total: number;
	readonly events: readonly Spoken[];
}

/** What a manager may do for its reports (O-D10): each power is off until it is given. */
export interface Powers {
	readonly assign: boolean;
	readonly answer: boolean;
	readonly review: boolean;
	readonly plan: boolean;
}

/** An agent, as its own page and its editor read it (27.9). */
export interface AgentDetail {
	readonly name: string;
	readonly title: string;
	readonly runner: string;
	readonly model: string;
	readonly host: string;
	readonly role: string;
	/** The agent it reports to (null: the humans). */
	readonly manager: string | null;
	readonly manages: Powers;
	readonly projects: readonly string[];
	readonly brief: string;
	readonly max_nudges: number;
	readonly enabled: boolean;
	readonly paused: boolean;
}

/** An agent, with what its page draws: its status, its session, its queue and its reports. */
export interface AgentInfo {
	readonly agent: AgentDetail;
	readonly status: string;
	readonly session: SessionSummary | null;
	readonly queue: readonly QueueEntry[];
	readonly reports: readonly string[];
}

/** A model a harness can run on a host, as `orch_list_runners` sends it (25.11). */
export interface RunnerModel {
	readonly id: string;
	readonly name: string;
}

/** A harness on a host: whether it is there, signed in, and what it can run. */
export interface Runner {
	readonly host: string;
	readonly runner: string;
	readonly installed: boolean;
	readonly signed_in: boolean;
	readonly version: string;
	readonly models: readonly RunnerModel[];
}

/** One row of a merge list, as the operation sends it. */
function mergeRowOf(value: JsonValue, project: string): MergeRow {
	const row = asObject(value);
	return {
		project,
		task: asText(row.task),
		feature: asText(row.feature),
		title: asText(row.title),
		number: asNumber(row.number),
		url: asText(row.url),
		sha: asText(row.sha),
		state: asText(row.state),
		blockers: asArray(row.blockers).map((one) => asText(one)),
		merger: typeof row.merger === "string" ? row.merger : null,
	};
}

/** A merge list, as `orch_merge_list` and `orch_inbox` send it. */
export function mergeListOf(value: JsonValue | undefined): MergeList {
	const list = asObject(value);
	const project = asText(list.project);
	return {
		project,
		auto_merge: asFlag(list.auto_merge),
		merger: typeof list.merger === "string" ? list.merger : null,
		ready: asArray(list.ready).map((one) => mergeRowOf(one, project)),
		coming: asArray(list.coming).map((one) => mergeRowOf(one, project)),
		held: asArray(list.held).map((one) => mergeRowOf(one, project)),
		done: asArray(list.done).map((one) => mergeRowOf(one, project)),
	};
}

/** A question, as `orch_list_questions` and `orch_inbox` send it. */
export function questionOf(value: JsonValue): Question {
	const one = asObject(value);
	return {
		key: asText(one.key),
		project: asText(one.project),
		title: asText(one.title),
		text: asText(one.text),
		asked_by: asText(one.asked_by),
		to: asText(one.to),
		state: asText(one.state),
		task: typeof one.task === "string" ? one.task : null,
		options: asArray(one.options).map((option) => asText(option)),
		blocking: asFlag(one.blocking),
		answer: typeof one.answer === "string" ? one.answer : null,
		answered_by: typeof one.answered_by === "string" ? one.answered_by : null,
		outcome: typeof one.outcome === "string" ? one.outcome : null,
		escalated: asFlag(one.escalated),
	};
}

/** A question with everything about it, as `orch_get_question` answers it. */
export function questionThreadOf(value: JsonValue | undefined): QuestionThread {
	const one = asObject(value);
	return {
		question: questionOf(one.question ?? null),
		thread: asArray(one.thread).map(messageOf),
		tasks: asArray(one.tasks).map((task) => asText(task)),
		features: asArray(one.features).map((feature) => asText(feature)),
	};
}

/** One thing said on a thread, as `orch_get_question` sends it. */
function messageOf(value: JsonValue): Message {
	const one = asObject(value);
	return { text: asText(one.text), author: asText(one.author), at: asText(one.at) };
}

/** An action, as `orch_list_actions` sends it. */
function actionOf(value: JsonValue): Action {
	const one = asObject(value);
	return {
		key: asText(one.key),
		title: asText(one.title),
		text: asText(one.text),
		when: asText(one.when),
		after: typeof one.after === "string" ? one.after : null,
		task: typeof one.task === "string" ? one.task : null,
		assignee: asText(one.assignee),
	};
}

/** An agent's line, as the inbox's strip sends it. */
function agentLineOf(value: JsonValue): AgentLine {
	const one = asObject(value);
	return {
		name: asText(one.name),
		title: asText(one.title),
		role: asText(one.role),
		state: asText(one.state),
		task: typeof one.task === "string" ? one.task : null,
		line: asText(one.line),
	};
}

/** The inbox, as `orch_inbox` answers it. */
export function inboxOf(value: JsonValue | undefined): Inbox {
	const inbox = asObject(value);
	return {
		project: asText(inbox.project),
		merge: mergeListOf(inbox.merge),
		questions: asArray(inbox.questions).map(questionOf),
		actions: asArray(inbox.actions).map(actionOf),
		agents: asArray(inbox.agents).map(agentLineOf),
		updated_at: asText(inbox.updated_at),
	};
}

/** A feature, as `orch_list_features` and a feature's page send it. */
function featureOf(value: JsonValue | undefined): Feature {
	const one = asObject(value);
	return {
		key: asText(one.key),
		project: asText(one.project),
		title: asText(one.title),
		status: asText(one.status),
	};
}

/** A task, as `orch_list_tasks` sends it. */
function taskOf(value: JsonValue | undefined): Task {
	const one = asObject(value);
	return {
		key: asText(one.key),
		project: asText(one.project),
		feature: asText(one.feature),
		title: asText(one.title),
		state: asText(one.state),
		assignee: typeof one.assignee === "string" ? one.assignee : null,
		size: asSize(one.size),
		needs: asArray(one.needs).map((need) => asText(need)),
		spec: asText(one.spec),
		hold: typeof one.hold === "string" && one.hold !== "" ? one.hold : null,
		blocked_on: typeof one.blocked_on === "string" ? one.blocked_on : null,
		repo: asText(one.repo),
		branch: asText(one.branch),
		worktree: asText(one.worktree),
		task_kind: asText(one.task_kind),
	};
}

/** A task on a queue, as `AgentInfo.queue` sends it. */
function queueEntryOf(value: JsonValue): QueueEntry {
	const one = asObject(value);
	return {
		key: asText(one.key),
		project: asText(one.project),
		feature: asText(one.feature),
		title: asText(one.title),
		state: asText(one.state),
		position: typeof one.position === "number" ? one.position : null,
	};
}

/** An agent, as `orch_list_agents` sends it (its `AgentInfo`, narrowed to what the board shows). */
function agentOf(value: JsonValue): Agent {
	const info = asObject(value);
	const agent = asObject(info.agent);
	return {
		name: asText(agent.name),
		title: asText(agent.title),
		role: asText(agent.role),
		status: asText(info.status),
		model: asText(agent.model),
		queue: asArray(info.queue).map(queueEntryOf),
		reports: asArray(info.reports).map((one) => asText(one)),
	};
}

/** A feature's points, as `orch_progress` sends them. */
function featurePointsOf(value: JsonValue): FeaturePoints {
	const one = asObject(value);
	return { key: asText(one.key), points: asNumber(one.points), merged: asNumber(one.merged) };
}

/** What a point costs a model, as `orch_progress` sends it. */
function modelRateOf(value: JsonValue): ModelRate {
	const one = asObject(value);
	return {
		model: asText(one.model),
		hours_per_point: asNumber(asObject(one.hours_per_point).hours),
	};
}

/** The board's share of a project's progress, as `orch_progress` sends it. */
export function progressOf(value: JsonValue | undefined): Progress {
	const progress = asObject(value);
	return {
		features: asArray(progress.features).map(featurePointsOf),
		models: asArray(progress.models).map(modelRateOf),
	};
}

/** A suggestion, as `orch_suggest` sends it. */
function suggestionOf(value: JsonValue): Suggestion {
	const one = asObject(value);
	return {
		task: asText(one.task),
		title: asText(one.title),
		feature: asText(one.feature),
		agent: asText(one.agent),
		score: asNumber(one.score),
		reasons: asArray(one.reasons).map((reason) => asText(reason)),
		stack_on: typeof one.stack_on === "string" ? one.stack_on : null,
	};
}

/** One thing a session said, as its transcript holds it. */
function spokenOf(value: JsonValue): Spoken {
	const item = asObject(value);
	const said = asObject(item.event);
	return {
		turn: asNumber(item.turn),
		at: asText(item.at),
		kind: asText(said.kind),
		text: asText(said.text),
		name: asText(said.name),
		summary: asText(said.summary),
		ok: typeof said.ok === "boolean" ? said.ok : null,
		cost: asDecimal(said.cost),
		tokens_in: asNumber(said.input_tokens),
		tokens_out: asNumber(said.output_tokens),
		reason: asText(said.reason),
		message: asText(said.message),
		until: typeof said.until === "string" ? said.until : null,
	};
}

/** A session, as an agent's own answer carries it (no transcript: `sessionOf` reads that). */
function sessionSummaryOf(value: JsonValue | undefined): SessionSummary | null {
	if (value === undefined || value === null) {
		return null;
	}
	const one = asObject(value);
	return {
		id: asText(one.id),
		task: asText(one.task),
		state: asText(one.state),
		turns: asNumber(one.turns),
		tokens_in: asNumber(one.tokens_in),
		tokens_out: asNumber(one.tokens_out),
		cost: asDecimal(one.cost),
		last_text: asText(one.last_text),
	};
}

/** A manager's powers, each off unless it is given (O-D10). */
function powersOf(value: JsonValue | undefined): Powers {
	const one = asObject(value);
	return {
		assign: asFlag(one.assign),
		answer: asFlag(one.answer),
		review: asFlag(one.review),
		plan: asFlag(one.plan),
	};
}

/** An agent's own record, as `orch_get_agent` and its editor read it. */
function agentDetailOf(value: JsonValue | undefined): AgentDetail {
	const one = asObject(value);
	return {
		name: asText(one.name),
		title: asText(one.title),
		runner: asText(one.runner),
		model: asText(one.model),
		host: asText(one.host),
		role: asText(one.role),
		manager: typeof one.manager === "string" && one.manager !== "" ? one.manager : null,
		manages: powersOf(one.manages),
		projects: asArray(one.projects).map((name) => asText(name)),
		brief: asText(one.brief),
		max_nudges: asNumber(one.max_nudges),
		enabled: one.enabled === undefined ? true : asFlag(one.enabled),
		paused: asFlag(one.paused),
	};
}

/** An agent, as `orch_get_agent` answers it. */
export function agentInfoOf(value: JsonValue | undefined): AgentInfo {
	const info = asObject(value);
	return {
		agent: agentDetailOf(info.agent),
		status: asText(info.status),
		session: sessionSummaryOf(info.session),
		queue: asArray(info.queue).map(queueEntryOf),
		reports: asArray(info.reports).map((name) => asText(name)),
	};
}

/** Every agent, as `orch_list_agents` answers them, by name. */
export function agentsOf(value: JsonValue | undefined): AgentInfo[] {
	return asArray(asObject(value).items).map(agentInfoOf);
}

/** The pull open on each task, from `orch_merge_list`'s three open lists (a merged or done one
 * is not open, so it is not there). */
export function openPullsOf(merge: MergeList): Record<string, OpenPull> {
	const pulls: Record<string, OpenPull> = {};
	for (const row of [...merge.ready, ...merge.coming, ...merge.held]) {
		if (row.task === "") {
			continue;
		}
		pulls[`${row.project}/${row.task}`] = { number: row.number, url: row.url };
	}
	return pulls;
}

/** A project's tasks, as `orch_list_tasks` answers them. */
export function tasksOf(value: JsonValue | undefined): Task[] {
	return asArray(asObject(value).tasks).map(taskOf);
}

/** The harnesses, as `orch_list_runners` answers them (25.11): what an agent's editor offers. */
export function runnersOf(value: JsonValue | undefined): Runner[] {
	return asArray(asObject(value).items).map((item) => {
		const one = asObject(item);
		const health = asObject(one.health);
		return {
			host: asText(one.host),
			runner: asText(one.runner),
			installed: asFlag(health.installed),
			signed_in: asFlag(health.signed_in),
			version: asText(health.version),
			models: asArray(one.models).map((model) => {
				const each = asObject(model);
				return { id: asText(each.id), name: asText(each.name) };
			}),
		};
	});
}

/** Who an agent reports up through, nearest first, ending at the humans (empty: it is a root). */
export function chainOf(agents: readonly AgentInfo[], name: string): string[] {
	const found = agents.find((one) => one.agent.name === name);
	if (found === undefined) {
		return [];
	}
	const chain: string[] = [];
	let above = found.agent.manager;
	// (A stored tree has no cycles — udl refuses to make one — but a page is not a place to
	// loop forever if one is ever read: a name already walked ends the chain.)
	while (above !== null && !chain.includes(above) && chain.length < agents.length) {
		chain.push(above);
		above = agents.find((one) => one.agent.name === above)?.agent.manager ?? null;
	}
	return chain;
}

/** Whether `above` is somewhere up `name`'s chain: the check a drag makes before it asks. */
export function isAbove(agents: readonly AgentInfo[], above: string, name: string): boolean {
	return chainOf(agents, name).includes(above);
}

/** A session, as `orch_get_session` answers it. */
export function sessionOf(value: JsonValue | undefined): Session {
	const one = asObject(value);
	const ref = asObject(one.ref);
	return {
		id: asText(one.id),
		agent: asText(one.agent),
		task: asText(one.task),
		state: asText(one.state),
		turns: asNumber(one.turns),
		tokens_in: asNumber(one.tokens_in),
		tokens_out: asNumber(one.tokens_out),
		cost: asDecimal(one.cost),
		last_text: asText(one.last_text),
		created_at: asText(one.created_at),
		limited_until: typeof one.limited_until === "string" ? one.limited_until : null,
		ref: {
			runner: asText(ref.runner),
			id: asText(ref.id),
			cwd: asText(ref.cwd),
			host: asText(ref.host),
		},
		total: asNumber(one.total),
		events: asArray(one.events).map(spokenOf),
	};
}

/** What the board reads, from the four answers it made. */
export function boardOf(
	features: JsonValue | undefined,
	tasks: JsonValue | undefined,
	agents: JsonValue | undefined,
	suggestions: JsonValue | undefined,
	merge?: JsonValue | undefined,
	progress?: JsonValue | undefined,
): Board {
	return {
		project: asText(asObject(features).project),
		features: asArray(asObject(features).features).map(featureOf),
		tasks: asArray(asObject(tasks).tasks).map(taskOf),
		agents: asArray(asObject(agents).items).map(agentOf),
		suggestions: asArray(asObject(suggestions).suggestions).map(suggestionOf),
		merge: mergeListOf(merge),
		progress: progressOf(progress),
	};
}

/** A note, as a feature's or a task's answer sends it. */
function noteOf(value: JsonValue): Note {
	const one = asObject(value);
	return {
		at: asText(one.at),
		author: asText(one.author),
		note_kind: asText(one.note_kind),
		text: asText(one.text),
	};
}

/** What is known of a pull, from a link's own `state`. */
function pullStateOf(state: JsonValue): PullState {
	const one = asObject(state);
	return {
		...(typeof one.checks === "string" ? { checks: one.checks } : {}),
		...(typeof one.review === "string" ? { review: one.review } : {}),
	};
}

/** A link, as a feature's or a task's answer sends it. */
function linkOf(value: JsonValue): Link {
	const one = asObject(value);
	const state = asObject(one.state);
	return {
		link_kind: asText(one.link_kind),
		url: asText(one.url),
		title: asText(one.title),
		state: pullStateOf(state),
	};
}

/** What a feature's tasks come to, as `orch_get_feature` sends it. */
function totalsOf(value: JsonValue | undefined): Totals {
	const totals = asObject(value);
	return {
		points: asNumber(totals.points),
		points_merged: asNumber(totals.points_merged),
		points_ready: asNumber(totals.points_ready),
	};
}

/** A feature, with what hangs on it (29.5's page). */
export function featureInfoOf(value: JsonValue | undefined): FeatureInfo {
	const info = asObject(value);
	const feature = asObject(info.feature);
	return {
		feature: featureOf(feature),
		spec: asText(feature.spec),
		status: asText(feature.status),
		totals: totalsOf(info.totals),
		tasks: asArray(info.tasks).map(taskOf),
		links: asArray(info.links).map(linkOf),
		notes: asArray(info.notes).map(noteOf),
	};
}

/** A task, with what hangs on it (29.5's page). `unblocks` is the keys of the tasks whose
 * `needs` name it: udl answers that from the feature's tasks, which the page reads too. */
export function taskInfoOf(value: JsonValue | undefined, unblocks: readonly string[]): TaskInfo {
	const info = asObject(value ?? {});
	const feature =
		info.feature === null || info.feature === undefined ? null : asObject(info.feature);
	return {
		task: taskOf(info.task),
		feature: feature === null ? null : featureOf(feature),
		links: asArray(info.links).map(linkOf),
		notes: asArray(info.notes).map(noteOf),
		questions: asArray(info.questions).map(questionOf),
		unblocks,
	};
}

/** What a task is expected to take, as `orch_estimate` sends it. */
export function estimateOf(value: JsonValue | undefined): Estimate {
	const one = asObject(value);
	return {
		size: asSize(one.size),
		estimate: typeof one.estimate === "string" ? one.estimate : null,
		worked: asText(one.worked),
	};
}

/** Whether what a task waits on is a question's key (`Q-<n>`), rather than a line of text. */
export function isQuestionKey(value: string): boolean {
	return /^Q-\d+$/.test(value);
}

/**
 * An ISO 8601 duration (`PT4H`, `PT1H20M`, `P1DT2H`) as a person reads it (`4h`, `1h 20m`,
 * `1d 2h`): what `orch_estimate` sends for what a task takes and has taken. Anything that is not
 * one is handed back as it came, so a caller may show it as it is.
 */
export function durationOf(value: string): string {
	const match = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/.exec(value);
	if (match === null) {
		return value;
	}
	const [, days, hours, minutes, seconds] = match;
	const parts: string[] = [];
	if (days !== undefined) {
		parts.push(`${days}d`);
	}
	if (hours !== undefined) {
		parts.push(`${hours}h`);
	}
	if (minutes !== undefined) {
		parts.push(`${minutes}m`);
	}
	if (seconds !== undefined) {
		parts.push(`${seconds}s`);
	}
	return parts.length === 0 ? "0m" : parts.join(" ");
}

/** Whether a pull may be merged, as `orch_readiness` sends it. */
export function readinessOf(value: JsonValue | undefined): Readiness {
	const one = asObject(value);
	return {
		ready: asFlag(one.ready),
		held: asFlag(one.held),
		blockers: asArray(one.blockers).map((blocker) => asText(blocker)),
		pending: asArray(one.pending).map((kind) => asText(kind)),
		open_items: asArray(one.open_items).map((value) => {
			const item = asObject(value);
			return {
				number: asText(item.number),
				status: asText(item.status),
				text: asText(item.text),
			};
		}),
		waited: asNumber(one.waited),
	};
}

/** The session a task is in, from `orch_list_agents`: the agent whose session is that task. */
export function sessionOfTask(agents: JsonValue | undefined, key: string): TaskSession | null {
	for (const item of asArray(asObject(agents).items)) {
		const info = asObject(item);
		if (info.session === null || info.session === undefined) {
			continue;
		}
		const session = asObject(info.session);
		if (asText(session.task) !== key) {
			continue;
		}
		return {
			agent: asText(asObject(info.agent).name),
			id: asText(session.id),
			state: asText(session.state),
		};
	}
	return null;
}
