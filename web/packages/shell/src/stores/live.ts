/**
 * Live data: what the page's connection to udl's event stream has brought in — the events
 * (a bounded buffer, oldest out), the runs and download jobs it keeps current by id, and
 * where the connection is (18.6's `live/connection.ts` fills it).
 */

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

/** Where the stream is: `offline` before it opens, `connecting` between tries, `live` on it. */
export type Connection = "offline" | "connecting" | "live";

/** An event from a stream: its name (`event`, `run`, or a download's job event), when the page
 * got it, and what udl sent. */
export interface LiveEvent {
	readonly name: string;
	readonly at: string;
	readonly data: unknown;
}

/** How many events the page keeps (a timeline, not the log). */
export const LIMIT = 500;

export interface LiveState {
	connection: Connection;
	events: readonly LiveEvent[];
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
