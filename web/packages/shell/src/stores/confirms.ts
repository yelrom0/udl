/**
 * "Are you sure?" as a promise: what a plugin's `ctx.confirm` is, and what the shell's own
 * pages can ask for. One question is on the page at a time; the ones asked while it waits are
 * answered in turn.
 */

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

/** A question waiting for its answer. */
export interface Question {
	readonly question: string;
	readonly ok: string;
	readonly cancel: string;
	readonly settle: (yes: boolean) => void;
}

export const confirms = createStore<{ asked: readonly Question[] }>(() => ({ asked: [] }));

/**
 * Ask `question`, and answer with what the user chose. The buttons are `options.ok` and
 * `options.cancel` (`OK` and `Cancel` when the caller does not say).
 */
export function ask(
	question: string,
	options?: { ok?: string; cancel?: string },
): Promise<boolean> {
	return new Promise((settle) => {
		confirms.setState(({ asked }) => ({
			asked: [
				...asked,
				{ question, ok: options?.ok ?? "OK", cancel: options?.cancel ?? "Cancel", settle },
			],
		}));
	});
}

/** Answer the question on the page, and let the next one be drawn. */
export function answer(yes: boolean): void {
	const [first, ...rest] = confirms.getState().asked;
	if (first === undefined) {
		return;
	}
	confirms.setState({ asked: rest });
	first.settle(yes);
}

/** The question on the page, as a hook (`null` when there is none). */
export const useQuestion = () => useStore(confirms, (state) => state.asked[0] ?? null);
