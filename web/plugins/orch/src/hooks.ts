import type { UIContext } from "@udl/ui-sdk";
import { useEffect, useState } from "react";
import { type BoardState, boardStore } from "./stores/board";
import { type InboxState, inboxStore, type Loaded } from "./stores/inbox";

/** The inbox before it is read: nothing yet, and nothing failed. */
const NO_INBOX: InboxState = { inbox: null, error: null };

/** The board before it is read. */
const NO_BOARD: BoardState = { features: [], tasks: [], agents: [], suggestions: [], error: null };

/**
 * A store made for the page it is drawn in: made on mount, its state followed, and stopped on
 * unmount (so a page that goes away stops listening to the live connection).
 */
function useLoaded<T>(ctx: UIContext, make: (ctx: UIContext) => Loaded<T>, initial: T): T {
	// (The state is the store's, from the first draw: `initial` is only what is drawn before the
	// effect runs, so it is not a dependency.)
	const [state, setState] = useState<T>(initial);
	useEffect(() => {
		const { store, stop } = make(ctx);
		const unsubscribe = store.subscribe((next) => {
			setState(next);
		});
		setState(store.getState());
		return () => {
			unsubscribe();
			stop();
		};
	}, [ctx, make]);
	return state;
}

/** The inbox (28.4), as a page or the widget reads it. */
export function useInbox(ctx: UIContext): InboxState {
	return useLoaded(ctx, inboxStore, NO_INBOX);
}

/** The board's four lists (29.4). */
export function useBoard(ctx: UIContext): BoardState {
	return useLoaded(ctx, boardStore, NO_BOARD);
}
