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
	/** Read again now: what a page asks after it changed something, to see it without waiting for
	 * the event that says so. */
	refresh(): Promise<void>;
	stop(): void;
}

/**
 * What changes what waits on a person: a pull readied or merged, a link refreshed (which is how
 * a pull's checks and the bot's review reach 28.3's merge list), the auto merge switch, a session
 * starting, ending or failing, an agent out of quota, and — from 28.5 — a question asked or
 * answered, a to-action needed and an agent gone idle. Not `orch.session.event`: that fires for
 * every line a runner streams, and `orch_inbox` reads GitHub.
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
	"orch.question.asked",
	"orch.question.answered",
	"orch.action.needed",
	"orch.agent.idle",
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
				const inbox = inboxOf(value);
				store.setState({ inbox, error: null });
				// (The drawer's question count (29.7's follow-up): what is still being asked. The
				// inbox is the page a person reads first, and it re-reads on 28.5's question
				// events, so the badge moves as questions are asked and answered — and there is
				// none while nothing waits.)
				const open = inbox.questions.filter((one) => one.state === "open").length;
				ctx.setNavCount("questions", open === 0 ? null : open);
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
