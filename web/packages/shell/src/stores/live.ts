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

/** What makes two notices the same event: its id, for an `event` notice and no other. A run's
 * or a job's notices are its history, and each is its own line. */
function keyOf(event: LiveEvent): string | null {
	if (event.name !== "event" || typeof event.data !== "object" || event.data === null) {
		return null;
	}
	const id = (event.data as { id?: unknown }).id;
	return typeof id === "string" ? id : null;
}

/**
 * Add an event to the timeline, dropping the oldest when it is full.
 *
 * 18.6's stream sends one event twice by design — when it is stored, and again when it is
 * dispatched — so the timeline keeps the event once, at its latest copy, in the place it
 * first took: a list of what happened, not of what udl said about it.
 */
export function pushEvent(event: LiveEvent): void {
	live.setState(({ events }) => {
		const key = keyOf(event);
		const at = key === null ? -1 : events.findIndex((one) => keyOf(one) === key);
		const kept = at === -1 ? [...events, event] : events.map((one, i) => (i === at ? event : one));
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
