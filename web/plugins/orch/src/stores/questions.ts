import type { UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asArray, asObject, asText, messageOf } from "../json";
import { type Question, questionOf } from "../model";
import type { Loaded } from "./inbox";
import { coalesced, on } from "./once";

/** What the questions store holds: the project they are in, every question (null until the first
 * read lands), and what reading them failed with. */
export interface QuestionsState {
	readonly project: string;
	readonly questions: readonly Question[] | null;
	readonly error: string | null;
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
	const store = createStore<QuestionsState>(() => ({ project: "", questions: null, error: null }));
	let stopped = false;
	const reads = coalesced(
		async () => {
			// (`state: null` — every state: the page shows the answered ones too, which is the
			// point of it. The operation's default is the open ones alone.)
			const value = await ctx.call("orch_list_questions", { state: null });
			if (!stopped) {
				const body = asObject(value);
				const questions = asArray(body.questions).map(questionOf);
				store.setState({ project: asText(body.project), questions, error: null });
				// (The drawer's question count (29.7's follow-up): what is still being asked. Set
				// here as well as on the inbox, so it is live on whichever of the two pages is
				// open — and none while nothing waits.)
				const open = questions.filter((one) => one.state === "open").length;
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
