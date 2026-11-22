import type { UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asObject, asText, messageOf } from "../json";
import { type Session, sessionOf } from "../model";
import type { Loaded } from "./inbox";

/** What the sessions store holds: each session it was asked for, by its id. */
export interface SessionsState {
	readonly sessions: Readonly<Record<string, Session>>;
	readonly error: string | null;
}

/** A sessions store, and `read`: the session a page is showing. */
export interface Sessions extends Loaded<SessionsState> {
	read(session: string): Promise<void>;
}

/**
 * The sessions a page is showing (29.6), read by id and read again when that session says
 * something: `orch.session.event` and the rest name the session they are about, so a page
 * showing one is not made to read every other's.
 */
export function sessionsStore(ctx: UIContext): Sessions {
	const store = createStore<SessionsState>(() => ({ sessions: {}, error: null }));
	const watched = new Set<string>();
	let stopped = false;
	const read = async (session: string): Promise<void> => {
		watched.add(session);
		try {
			const value = await ctx.call("orch_get_session", { session });
			if (!stopped) {
				store.setState((state) => ({
					sessions: { ...state.sessions, [session]: sessionOf(value) },
					error: null,
				}));
			}
		} catch (error) {
			if (!stopped) {
				store.setState({ error: messageOf(error) });
			}
		}
	};
	const unsubscribe = ctx.events.subscribe("orch.session.*", (event) => {
		const named = asText(asObject(event.data).session);
		for (const session of [...watched]) {
			if (named === "" || named === session) {
				void read(session);
			}
		}
	});
	return {
		store,
		read,
		stop: () => {
			stopped = true;
			unsubscribe();
		},
	};
}
