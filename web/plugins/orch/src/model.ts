/**
 * What the plugin reads of udl's answers: the orchestrator's own shapes, as the operations send
 * them (a JSON object each), narrowed here so a page never reads a field blind. Only what the
 * plugin draws is here; the rest stays `JsonValue` until a page needs it.
 */

import type { JsonValue } from "@udl/ui-sdk";
import { asArray, asFlag, asNumber, asObject, asText } from "./json";

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
	readonly text: string;
	readonly to: string;
	readonly state: string;
	readonly task: string | null;
	readonly options: readonly string[];
}

/** What only a person can do (28.2). */
export interface Action {
	readonly key: string;
	readonly title: string;
	readonly when: string;
	readonly after: string | null;
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
	readonly title: string;
	readonly status: string;
}

/** A task: one piece of a feature (26.3, 26.8). */
export interface Task {
	readonly key: string;
	readonly feature: string;
	readonly title: string;
	readonly state: string;
	readonly assignee: string | null;
	readonly size: Size | null;
	readonly needs: readonly string[];
}

/** An agent, as the board shows it (27.1). */
export interface Agent {
	readonly name: string;
	readonly title: string;
	readonly role: string;
	readonly status: string;
}

/** A task suggested next, with the agent it suits (27.4). */
export interface Suggestion {
	readonly task: string;
	readonly title: string;
	readonly feature: string;
	readonly agent: string;
	readonly reasons: readonly string[];
}

/** What the board reads: features, tasks, agents and suggestions. */
export interface Board {
	readonly features: readonly Feature[];
	readonly tasks: readonly Task[];
	readonly agents: readonly Agent[];
	readonly suggestions: readonly Suggestion[];
}

/** A session, as `orch_get_session` answers it (27.7): where it is and what it last said. */
export interface Session {
	readonly id: string;
	readonly agent: string;
	readonly task: string;
	readonly state: string;
	readonly turns: number;
	readonly last_text: string;
}

/** One row of a merge list, as the operation sends it. */
function mergeRowOf(value: JsonValue): MergeRow {
	const row = asObject(value);
	return {
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
	return {
		project: asText(list.project),
		auto_merge: asFlag(list.auto_merge),
		merger: typeof list.merger === "string" ? list.merger : null,
		ready: asArray(list.ready).map(mergeRowOf),
		coming: asArray(list.coming).map(mergeRowOf),
		held: asArray(list.held).map(mergeRowOf),
		done: asArray(list.done).map(mergeRowOf),
	};
}

/** A question, as `orch_list_questions` sends it. */
function questionOf(value: JsonValue): Question {
	const one = asObject(value);
	return {
		key: asText(one.key),
		text: asText(one.text),
		to: asText(one.to),
		state: asText(one.state),
		task: typeof one.task === "string" ? one.task : null,
		options: asArray(one.options).map((option) => asText(option)),
	};
}

/** An action, as `orch_list_actions` sends it. */
function actionOf(value: JsonValue): Action {
	const one = asObject(value);
	return {
		key: asText(one.key),
		title: asText(one.title),
		when: asText(one.when),
		after: typeof one.after === "string" ? one.after : null,
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

/** A feature, as `orch_list_features` sends it. */
function featureOf(value: JsonValue): Feature {
	const one = asObject(value);
	return { key: asText(one.key), title: asText(one.title), status: asText(one.status) };
}

/** A task, as `orch_list_tasks` sends it. */
function taskOf(value: JsonValue): Task {
	const one = asObject(value);
	return {
		key: asText(one.key),
		feature: asText(one.feature),
		title: asText(one.title),
		state: asText(one.state),
		assignee: typeof one.assignee === "string" ? one.assignee : null,
		size: asSize(one.size),
		needs: asArray(one.needs).map((need) => asText(need)),
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
		reasons: asArray(one.reasons).map((reason) => asText(reason)),
	};
}

/** A session, as `orch_get_session` answers it. */
export function sessionOf(value: JsonValue | undefined): Session {
	const one = asObject(value);
	return {
		id: asText(one.id),
		agent: asText(one.agent),
		task: asText(one.task),
		state: asText(one.state),
		turns: asNumber(one.turns),
		last_text: asText(one.last_text),
	};
}

/** What the board reads, from the four answers it made. */
export function boardOf(
	features: JsonValue | undefined,
	tasks: JsonValue | undefined,
	agents: JsonValue | undefined,
	suggestions: JsonValue | undefined,
): Board {
	return {
		features: asArray(asObject(features).features).map(featureOf),
		tasks: asArray(asObject(tasks).tasks).map(taskOf),
		agents: asArray(asObject(agents).items).map(agentOf),
		suggestions: asArray(asObject(suggestions).suggestions).map(suggestionOf),
	};
}
