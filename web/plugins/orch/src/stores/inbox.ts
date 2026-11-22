import type { UIContext } from "@udl/ui-sdk";
import { createStore, type StoreApi } from "zustand/vanilla";
import { messageOf } from "../json";
import { type Inbox, inboxOf } from "../model";

/** What the inbox store holds: the inbox, or what reading it failed with. */
export interface InboxState {
	readonly inbox: Inbox | null;
	readonly error: string | null;
}

/** A store, and what stops it listening. */
export interface Loaded<T> {
	readonly store: StoreApi<T>;
	stop(): void;
}

/**
 * The inbox (28.4), read once when the store is made and again on every `orch.*` event: what
 * waits on a person changes when a task moves, a pull is readied or a question is asked, and
 * every one of those is orch's own.
 */
export function inboxStore(ctx: UIContext): Loaded<InboxState> {
	const store = createStore<InboxState>(() => ({ inbox: null, error: null }));
	let stopped = false;
	const load = async (): Promise<void> => {
		try {
			const value = await ctx.call("orch_inbox", {});
			if (!stopped) {
				store.setState({ inbox: inboxOf(value), error: null });
			}
		} catch (error) {
			if (!stopped) {
				store.setState({ error: messageOf(error) });
			}
		}
	};
	const unsubscribe = ctx.events.subscribe("orch.*", () => {
		void load();
	});
	void load();
	return {
		store,
		stop: () => {
			stopped = true;
			unsubscribe();
		},
	};
}
