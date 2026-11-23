import type { UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asObject, asText, messageOf } from "../json";
import { type Session, sessionOf } from "../model";
import type { Loaded } from "./inbox";
import { coalesced } from "./once";

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
	const pending = new Set<string>();
	const reads = coalesced(
		async () => {
			// (Every session asked for is read once: one event naming none is a read per session
			// watched, not a read of whichever was asked for last.)
			while (pending.size > 0) {
				const ids = [...pending];
				pending.clear();
				let failed: unknown = null;
				for (const session of ids) {
					try {
						const value = await ctx.call("orch_get_session", { session });
						if (!stopped) {
							store.setState((state) => ({
								sessions: { ...state.sessions, [session]: sessionOf(value) },
								error: null,
							}));
						}
					} catch (error) {
						// (One session failing must not leave the rest of the batch unread.)
						failed = failed ?? error;
					}
				}
				if (failed !== null) {
					throw failed;
				}
			}
		},
		(error) => {
			if (!stopped) {
				store.setState({ error: messageOf(error) });
			}
		},
	);
	const ask = (session: string): Promise<void> => {
		watched.add(session);
		pending.add(session);
		return reads.ask();
	};
	const read = async (session: string): Promise<void> => {
		await ask(session);
	};
	const unsubscribe = ctx.events.subscribe("orch.session.*", (event) => {
		const named = asText(asObject(event.data).session);
		for (const session of [...watched]) {
			if (named === "" || named === session) {
				void ask(session);
			}
		}
	});
	return {
		store,
		read,
		stop: () => {
			stopped = true;
			reads.stop();
			unsubscribe();
		},
	};
}
