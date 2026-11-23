import type { UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asObject, asText, messageOf } from "../json";
import {
	type Agent,
	type Board,
	boardOf,
	type Feature,
	mergeListOf,
	progressOf,
	type Suggestion,
	type Task,
} from "../model";
import type { Loaded } from "./inbox";
import { coalesced, on } from "./once";

/** What the board store holds: a project's features, tasks, agents and suggestions. */
export interface BoardState extends Board {
	readonly error: string | null;
}

/** What the board reads once it knows the project: its five calls, made together. */
const READS: readonly string[] = [
	"orch_list_tasks",
	"orch_list_agents",
	"orch_suggest",
	"orch_merge_list",
	"orch_progress",
];

/**
 * What moves what the board draws: a pull readied or merged, a link refreshed, and an agent's
 * session starting, ending, failing or running out of quota — which is where its status comes
 * from. `orch.session.event` is a line a runner streamed, not a status change. (28.5's
 * `orch.task.changed` is watched by 29.4's board, where a task's state is drawn.)
 */
const WATCHED: readonly string[] = [
	"orch.task.ready",
	"orch.task.merged",
	"orch.link.changed",
	"orch.session.started",
	"orch.session.ended",
	"orch.session.failed",
	"orch.agent.limited",
	"orch.task.changed",
	"orch.task.blocked",
];

/** The board's four lists (29.4), read when the store is made and again when any of them moves. */
export function boardStore(ctx: UIContext): Loaded<BoardState> {
	const store = createStore<BoardState>(() => ({
		project: "",
		features: [] as readonly Feature[],
		tasks: [] as readonly Task[],
		agents: [] as readonly Agent[],
		suggestions: [] as readonly Suggestion[],
		merge: mergeListOf(undefined),
		progress: progressOf(undefined),
		error: null,
	}));
	let stopped = false;
	const reads = coalesced(
		async () => {
			// (The project's features come first: every other read is asked for that project, so
			// a board of every project would draw one's tasks in another's columns.)
			const features = await ctx.call("orch_list_features", {});
			const project = asText(asObject(features).project);
			const [tasks, agents, suggestions, merge, progress] = await Promise.all(
				READS.map((name) => ctx.call(name, { project })),
			);
			if (!stopped) {
				store.setState({
					...boardOf(features, tasks, agents, suggestions, merge, progress),
					error: null,
				});
			}
		},
		(error) => {
			if (!stopped) {
				store.setState({ error: messageOf(error) });
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
