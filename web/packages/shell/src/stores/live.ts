/**
 * Live data: the page's one connection to udl's event stream (18.6 opens it), the events it
 * has brought in (a bounded buffer, oldest out), and the runs and download jobs it keeps
 * current by id.
 */

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

/** Where the stream is: `offline` before it opens, `connecting` between tries, `live` on it. */
export type Connection = "offline" | "connecting" | "live";

/** An event from the stream, as udl sends it. */
export interface LiveEvent {
	readonly id: number;
	readonly type: string;
	readonly at: string;
	readonly data: unknown;
}

/** How many events the page keeps (a timeline, not the log). */
export const LIMIT = 200;

export interface LiveState {
	connection: Connection;
	events: readonly LiveEvent[];
	// (What a run or a download job is doing now, by id: 18.6 and 19 fill these.)
	runs: Readonly<Record<string, unknown>>;
	jobs: Readonly<Record<string, unknown>>;
}

export const live = createStore<LiveState>(() => ({
	connection: "offline",
	events: [],
	runs: {},
	jobs: {},
}));

export function setConnection(connection: Connection): void {
	live.setState({ connection });
}

/** Add an event to the timeline, dropping the oldest when it is full. */
export function pushEvent(event: LiveEvent): void {
	live.setState(({ events }) => {
		const kept = [...events, event];
		return { events: kept.length > LIMIT ? kept.slice(kept.length - LIMIT) : kept };
	});
}

/** Say what a run is doing now. */
export function setRun(id: string, run: unknown): void {
	live.setState(({ runs }) => ({ runs: { ...runs, [id]: run } }));
}

/** Say what a download job is doing now. */
export function setJob(id: string, job: unknown): void {
	live.setState(({ jobs }) => ({ jobs: { ...jobs, [id]: job } }));
}

/** Forget everything a connection brought in (a signed-out page's data is not kept). */
export function clear(): void {
	live.setState({ connection: "offline", events: [], runs: {}, jobs: {} });
}

/** The live state, as a hook (a selector picks what a component needs). */
export const useLive = <T>(pick: (state: LiveState) => T): T => useStore(live, pick);
