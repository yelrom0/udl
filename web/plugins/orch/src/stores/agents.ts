import type { UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { messageOf } from "../json";
import {
	type AgentInfo,
	type AgentUsage,
	agentInfoOf,
	agentsOf,
	type MergeList,
	mergeListOf,
	type Progress,
	progressOf,
	type Task,
	tasksOf,
	usagesOf,
} from "../model";
import type { Loaded } from "./inbox";
import { coalesced, on, orNone } from "./once";

/** What the agents' page holds: every agent, and whether the read came back. */
export interface AgentsState {
	readonly items: readonly AgentInfo[];
	/** What each agent has left (27.14), by agent: the line under its name. Empty when the
	 * provider could not be asked — a key's money is beside the tree, not part of it. */
	readonly usage: readonly AgentUsage[];
	/** Whether a read came back: until one has, the page is reading, not empty. */
	readonly loaded: boolean;
	readonly error: string | null;
}

/**
 * What changes the agents' page: an agent made, edited, paused, resumed or deleted (its own
 * change, so another tab's edit is this page's news), a session starting or ending, and an agent
 * going idle or running out of quota (what each is doing).
 */
const WATCHED: readonly string[] = [
	"orch.agent.changed",
	"orch.session.started",
	"orch.session.ended",
	"orch.session.failed",
	"orch.agent.idle",
	"orch.agent.limited",
];

/** The agents' page (29.6): the tree, read when the store is made and again when any of the
 * above moves. */
export function agentsStore(ctx: UIContext): Loaded<AgentsState> {
	const store = createStore<AgentsState>(() => ({
		items: [],
		usage: [],
		loaded: false,
		error: null,
	}));
	let stopped = false;
	const reads = coalesced(
		async () => {
			const [items, usage] = await Promise.all([
				ctx.call("orch_list_agents", {}),
				// (Beside the tree: `orch_usage` reads each agent's transcript, and a provider that
				// cannot be asked leaves the line unsaid rather than the page empty.)
				orNone(ctx.call("orch_usage", {})),
			]);
			if (!stopped) {
				store.setState({
					items: agentsOf(items),
					usage: usage === undefined ? [] : usagesOf(usage),
					loaded: true,
					error: null,
				});
			}
		},
		(error) => {
			if (!stopped) {
				store.setState({ loaded: true, error: messageOf(error) });
			}
		},
	);
	const unsubscribes = on(ctx, WATCHED, () => {
		void reads.ask();
	});
	void reads.ask();
	return {
		store,
		refresh: () => reads.ask(),
		stop: () => {
			stopped = true;
			reads.stop();
			for (const unsubscribe of unsubscribes) {
				unsubscribe();
			}
		},
	};
}

/** What one agent's page holds: the agent, the tree it sits in, what its queue's tasks are,
 * the merge lists its pulls are read from, and its own project's rates. */
export interface AgentState {
	readonly info: AgentInfo | null;
	/** What it has left (27.14): its key's money, or its free allowance. Null when udl could
	 * not say (the provider is down), which leaves its other cards standing. */
	readonly usage: AgentUsage | null;
	/** Every agent, so its place in the hierarchy is the whole chain and not just its manager. */
	readonly agents: readonly AgentInfo[];
	readonly tasks: readonly Task[];
	readonly merge: MergeList;
	readonly progress: Progress;
	readonly loaded: boolean;
	readonly error: string | null;
}

/** What changes an agent's page: the agent itself, its session starting or ending (each line of
 * a running one is the session's own store's business), and the tasks on its queue. */
const ONE_WATCHED: readonly string[] = [
	"orch.agent.changed",
	"orch.session.started",
	"orch.session.ended",
	"orch.session.failed",
	"orch.agent.idle",
	"orch.agent.limited",
	"orch.task.changed",
	"orch.task.blocked",
	"orch.task.ready",
	"orch.task.merged",
	"orch.link.changed",
];

/**
 * An agent's page (29.6): the agent, the tree (for its chain), and what its queue shows — each
 * task's size and its current task's open pull — read from every project its queue spans, since
 * a task's key is only unique within one. Its own project's rates are the first of them: the one
 * its current task is in.
 */
export function agentStore(ctx: UIContext, name: string): Loaded<AgentState> {
	const store = createStore<AgentState>(() => ({
		info: null,
		usage: null,
		agents: [],
		tasks: [],
		merge: mergeListOf(undefined),
		progress: progressOf(undefined),
		loaded: false,
		error: null,
	}));
	let stopped = false;
	const reads = coalesced(
		async () => {
			const answer = await ctx.call("orch_get_agent", { name });
			const info = agentInfoOf(answer);
			const projects = [
				...new Set(info.queue.map((one) => one.project).filter((one) => one !== "")),
			];
			// (Each of these is beside the agent, not the agent: one udl cannot answer — no
			// GitHub account is connected, say — leaves its own card unsaid, and the page stands.)
			const [list, usage, ...beside] = await Promise.all([
				orNone(ctx.call("orch_list_agents", {})),
				orNone(ctx.call("orch_usage", { agent: name })),
				...projects.map(async (project) => {
					const [found, pulls, rates] = await Promise.all([
						orNone(ctx.call("orch_list_tasks", { project })),
						orNone(ctx.call("orch_merge_list", { project })),
						orNone(ctx.call("orch_progress", { project })),
					]);
					return { found, pulls, rates };
				}),
			]);
			const tasks = beside.flatMap((one) => (one.found === undefined ? [] : tasksOf(one.found)));
			const pulls = beside.map((one) => mergeListOf(one.pulls));
			// (A page draws the rows of every project its queue spans: the project and the switch
			// are the first's, which is the one its current task is in.)
			const merge: MergeList = {
				project: pulls[0]?.project ?? "",
				auto_merge: pulls[0]?.auto_merge ?? false,
				merger: pulls[0]?.merger ?? null,
				ready: pulls.flatMap((one) => one.ready),
				coming: pulls.flatMap((one) => one.coming),
				held: pulls.flatMap((one) => one.held),
				done: pulls.flatMap((one) => one.done),
			};
			if (!stopped) {
				store.setState({
					info,
					usage: usage === undefined ? null : (usagesOf(usage)[0] ?? null),
					agents: list === undefined ? [] : agentsOf(list),
					tasks,
					merge,
					progress: progressOf(beside[0]?.rates),
					loaded: true,
					error: null,
				});
			}
		},
		(error) => {
			if (!stopped) {
				store.setState({ loaded: true, error: messageOf(error) });
			}
		},
	);
	const unsubscribes = on(ctx, ONE_WATCHED, () => {
		void reads.ask();
	});
	void reads.ask();
	return {
		store,
		refresh: () => reads.ask(),
		stop: () => {
			stopped = true;
			reads.stop();
			for (const unsubscribe of unsubscribes) {
				unsubscribe();
			}
		},
	};
}
