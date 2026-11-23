import type { UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { messageOf } from "../json";
import {
	type Agent,
	type Board,
	boardOf,
	type Feature,
	type Suggestion,
	type Task,
} from "../model";
import type { Loaded } from "./inbox";
import { coalesced, on } from "./once";

/** What the board store holds: a project's features, tasks, agents and suggestions. */
export interface BoardState extends Board {
	readonly error: string | null;
}

/** What the board reads: its four calls, made together. */
const READS: readonly string[] = [
	"orch_list_features",
	"orch_list_tasks",
	"orch_list_agents",
	"orch_suggest",
];

/**
 * What moves what the board draws: a pull readied or merged, a link refreshed, and an agent's
 * session starting, ending, failing or running out of quota — which is where its status comes
 * from. A feature and a task's state have no event of their own yet (28.5), and
 * `orch.session.event` is a line a runner streamed, not a status change.
 */
const WATCHED: readonly string[] = [
	"orch.task.ready",
	"orch.task.merged",
	"orch.link.changed",
	"orch.session.started",
	"orch.session.ended",
	"orch.session.failed",
	"orch.agent.limited",
];

/** The board's four lists (29.4), read when the store is made and again when any of them moves. */
export function boardStore(ctx: UIContext): Loaded<BoardState> {
	const store = createStore<BoardState>(() => ({
		features: [] as readonly Feature[],
		tasks: [] as readonly Task[],
		agents: [] as readonly Agent[],
		suggestions: [] as readonly Suggestion[],
		error: null,
	}));
	let stopped = false;
	const reads = coalesced(
		async () => {
			const [features, tasks, agents, suggestions] = await Promise.all(
				READS.map((name) => ctx.call(name, {})),
			);
			if (!stopped) {
				store.setState({ ...boardOf(features, tasks, agents, suggestions), error: null });
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
