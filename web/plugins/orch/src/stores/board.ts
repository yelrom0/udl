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

/** What moves what the board draws (a task, a feature, an agent): its own events, not every one. */
const WATCHED: readonly string[] = ["orch.task.*", "orch.feature.*", "orch.agent.*"];

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
	const load = async (): Promise<void> => {
		try {
			const [features, tasks, agents, suggestions] = await Promise.all(
				READS.map((name) => ctx.call(name, {})),
			);
			if (!stopped) {
				store.setState({ ...boardOf(features, tasks, agents, suggestions), error: null });
			}
		} catch (error) {
			if (!stopped) {
				store.setState({ error: messageOf(error) });
			}
		}
	};
	const unsubscribes = WATCHED.map((filter) =>
		ctx.events.subscribe(filter, () => {
			void load();
		}),
	);
	void load();
	return {
		store,
		stop: () => {
			stopped = true;
			for (const unsubscribe of unsubscribes) {
				unsubscribe();
			}
		},
	};
}
