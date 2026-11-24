import type { UIContext } from "@udl/ui-sdk";
import { useState } from "react";
import { messageOf } from "../json";
import type { Question } from "../model";

/** What a page that answers questions is given: what is in flight, why one was refused, and the
 * answer itself (29.3, 29.7 — the same handler, so a question behaves the same on both). */
export interface Answering {
	/** The questions being answered, one per answer in flight: their buttons say so and take no
	 * click. */
	readonly answering: ReadonlySet<string>;
	/** A refusal, as udl said it (the question stays, and so does the reason). */
	readonly refused: string | null;
	/** Answer `question` with `choice`: `orch_answer`, then a read so the page shows it at once
	 * (rather than when `orch.question.answered` arrives). */
	answer(question: Question, choice: string): void;
}

/**
 * Answering a question (29.3's handler, shared with 29.7): each question's own answer, so one
 * landing does not take the "Answering…" off another that is still out — two different questions
 * may be answered at once.
 */
export function useAnswer(
	ctx: UIContext,
	project: string,
	refresh: () => Promise<void>,
): Answering {
	const [answering, setAnswering] = useState<ReadonlySet<string>>(() => new Set());
	const [refused, setRefused] = useState<string | null>(null);

	const answer = (question: Question, choice: string): void => {
		setAnswering((was) => new Set(was).add(question.key));
		setRefused(null);
		void (async () => {
			try {
				await ctx.call("orch_answer", { project, key: question.key, answer: choice });
			} catch (failure) {
				// (A refusal is udl's, and it says why: the question stays, and the reason does too.)
				setRefused(messageOf(failure));
			}
			// (The read comes before the button is taken back: a second click while it is out would
			// answer a question that is already answered. `orch.question.answered` would read it
			// too, but the button would wait for the event to arrive.)
			await refresh();
			setAnswering((was) => {
				const left = new Set(was);
				left.delete(question.key);
				return left;
			});
		})();
	};

	return { answering, refused, answer };
}
