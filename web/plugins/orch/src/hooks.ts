import type { UIContext } from "@udl/ui-sdk";
import { useEffect, useState } from "react";
import { mergeListOf, progressOf } from "./model";
import { type BoardState, boardStore } from "./stores/board";
import { type InboxState, inboxStore, type Loaded } from "./stores/inbox";

/** What a page that can change what it read is given: the state, and a way to read it again. */
export type Refreshable<T> = T & { refresh(): Promise<void> };

/** The inbox before it is read: nothing yet, and nothing failed. */
const NO_INBOX: InboxState = { inbox: null, error: null };

/** The board before it is read. */
const NO_BOARD: BoardState = {
	project: "",
	features: [],
	tasks: [],
	agents: [],
	suggestions: [],
	merge: mergeListOf(undefined),
	progress: progressOf(undefined),
	error: null,
};

/**
 * A store made for the page it is drawn in: made on mount, its state followed, and stopped on
 * unmount (so a page that goes away stops listening to the live connection).
 */
function useLoaded<T>(
	ctx: UIContext,
	make: (ctx: UIContext) => Loaded<T>,
	initial: T,
): Refreshable<T> {
	// (The state is the store's, from the first draw: `initial` is only what is drawn before the
	// effect runs, so it is not a dependency.)
	const [state, setState] = useState<T>(initial);
	const [refresh, setRefresh] = useState<() => Promise<void>>(() => async () => {});
	useEffect(() => {
		const { store, stop, refresh: again } = make(ctx);
		setRefresh(() => again);
		const unsubscribe = store.subscribe((next) => {
			setState(next);
		});
		setState(store.getState());
		return () => {
			unsubscribe();
			stop();
		};
	}, [ctx, make]);
	return { ...state, refresh };
}

/** The inbox (28.4), as a page or the widget reads it. */
export function useInbox(ctx: UIContext): Refreshable<InboxState> {
	return useLoaded(ctx, inboxStore, NO_INBOX);
}

/** The board's four lists (29.4). */
export function useBoard(ctx: UIContext): Refreshable<BoardState> {
	return useLoaded(ctx, boardStore, NO_BOARD);
}
