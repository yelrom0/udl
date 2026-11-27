import type { UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asArray, asObject, asText, messageOf } from "../json";
import { type Runner, runnersOf } from "../model";
import type { Loaded } from "./inbox";

/** What an editor reads: the harnesses, and what a host could not say about its own. */
export interface RunnersState {
	readonly items: readonly Runner[];
	/** What udl could not ask, one line each (a host that is down, say). */
	readonly problems: readonly string[];
	/** Whether a host answered at all: with none, an editor takes the runner's name as text. */
	readonly answered: boolean;
	readonly loaded: boolean;
	readonly error: string | null;
}

/**
 * The harnesses a person may name for an agent, as `orch_list_runners` answers them (25.11).
 *
 * `host` names the host to ask: a chat asks its own (29.12), because asking for none lists the
 * hosts udl has records of, and `local` is only made when it is asked for by name.
 */
export function runnersStore(ctx: UIContext, host?: string): Loaded<RunnersState> {
	const store = createStore<RunnersState>(() => ({
		items: [],
		problems: [],
		answered: false,
		loaded: false,
		error: null,
	}));
	let stopped = false;
	const read = async (): Promise<void> => {
		try {
			const answer = asObject(
				await ctx.call("orch_list_runners", host === undefined ? {} : { host }),
			);
			if (!stopped) {
				store.setState({
					items: runnersOf(answer),
					problems: asArray(answer.problems).map((one) => asText(one)),
					answered: true,
					loaded: true,
					error: null,
				});
			}
		} catch (failure) {
			// (A host that cannot be asked is not the editor's failure: it takes a name instead.)
			if (!stopped) {
				store.setState({ loaded: true, error: messageOf(failure) });
			}
		}
	};
	void read();
	return {
		store,
		refresh: read,
		stop: () => {
			stopped = true;
		},
	};
}
