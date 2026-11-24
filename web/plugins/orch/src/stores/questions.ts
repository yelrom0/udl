import type { UIContext } from "@udl/ui-sdk";
import { createStore, type StoreApi } from "zustand/vanilla";
import { asArray, asObject, messageOf } from "../json";
import { type Question, questionOf } from "../model";
import { coalesced, on } from "./once";

/** What the questions store holds: every question, or what reading them failed with. */
export interface QuestionsState {
	readonly questions: readonly Question[];
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
 * What changes what is being asked: a question asked (28.1) and one answered (28.1) — the two
 * events 28.5 publishes. A question is closed or escalated through the same records, so those
 * come with `orch.question.answered` or nothing; reading again after the page's own write covers
 * them.
 */
const WATCHED: readonly string[] = ["orch.question.asked", "orch.question.answered"];

/**
 * Every question (29.7), read once when the store is made and again when one of the events above
 * arrives: one read at a time, so a burst is one read rather than one per event.
 */
export function questionsStore(ctx: UIContext): Loaded<QuestionsState> {
	const store = createStore<QuestionsState>(() => ({ questions: [], error: null }));
	let stopped = false;
	const reads = coalesced(
		async () => {
			// (`state: null` — every state: the page shows the answered ones too, which is the
			// point of it. The operation's default is the open ones alone.)
			const value = await ctx.call("orch_list_questions", { state: null });
			if (!stopped) {
				store.setState({
					questions: asArray(asObject(value).questions).map(questionOf),
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
