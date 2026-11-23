import type { UIContext } from "@udl/ui-sdk";
import { createStore, type StoreApi } from "zustand/vanilla";
import { messageOf } from "../json";
import { type Inbox, inboxOf } from "../model";
import { coalesced, on } from "./once";

/** What the inbox store holds: the inbox, or what reading it failed with. */
export interface InboxState {
	readonly inbox: Inbox | null;
	readonly error: string | null;
}

/** A store, and what stops it listening. */
export interface Loaded<T> {
	readonly store: StoreApi<T>;
	/** Read again now: what a page asks after it changed something with no event of its own. */
	refresh(): Promise<void>;
	stop(): void;
}

/**
 * What changes what waits on a person: a pull readied or merged, a link refreshed (which is how
 * a pull's checks and the bot's review reach 28.3's merge list), the auto merge switch, a session
 * starting, ending or failing, and an agent out of quota (a question and a to-action have no
 * event of their own yet: 28.5 gives them one). Not `orch.session.event`: that fires for every
 * line a runner streams, and `orch_inbox` reads GitHub.
 */
const WATCHED: readonly string[] = [
	"orch.task.ready",
	"orch.task.merged",
	"orch.link.changed",
	"orch.project.changed",
	"orch.session.started",
	"orch.session.ended",
	"orch.session.failed",
	"orch.agent.limited",
];

/**
 * The inbox (28.4), read once when the store is made and again when one of the events above
 * moves it: one read at a time, so a burst is one read rather than one per event.
 */
export function inboxStore(ctx: UIContext): Loaded<InboxState> {
	const store = createStore<InboxState>(() => ({ inbox: null, error: null }));
	let stopped = false;
	const reads = coalesced(
		async () => {
			const value = await ctx.call("orch_inbox", {});
			if (!stopped) {
				store.setState({ inbox: inboxOf(value), error: null });
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
