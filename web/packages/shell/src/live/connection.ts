/**
 * Live data: udl's server-sent events, in the page. One `EventSource` per stream — udl's own
 * (`/events/stream`), and a download's while a page watches it — reconnected after 1 s,
 * doubling to 30 s with jitter, and the page's lists reloaded once a stream is back (it is
 * live only, so what it missed has to be asked for again).
 *
 * It feeds the `live` store: the last 500 events, the runs by id, a download's job by id, and
 * the connection state the navbar shows.
 */

import { clear, pushEvent, setConnection, setJob, setRun } from "../stores/live";

/** How long the first retry waits, and the longest one. */
export const FIRST_DELAY = 1_000;
export const MAX_DELAY = 30_000;

/** Where udl's events are. */
export const EVENTS_STREAM = "/events/stream";

/** What udl sends on a download's stream (a `JobEvent`'s type is the event's name). */
export const JOB_TYPES = [
	"queued",
	"started",
	"progress",
	"finished",
	"failed",
	"cancelled",
] as const;

/** What udl sends on its own stream. */
export const EVENT_TYPES = ["event", "run", "lagged"] as const;

/** A download's job events that end it: its stream is over, so the page stops following it. */
export const JOB_ENDS: readonly string[] = ["finished", "failed", "cancelled"];

/** The clocks the connection waits on (a test's own, so the backoff is not a wait). */
export interface Timers {
	readonly after: (run: () => void, ms: number) => ReturnType<typeof setTimeout>;
	readonly cancel: (timer: ReturnType<typeof setTimeout>) => void;
}

export const REAL_TIMERS: Timers = {
	after: (run, ms) => setTimeout(run, ms),
	cancel: (timer) => clearTimeout(timer),
};

/** What the connection needs of an `EventSource` (the browser's, or a test's own). */
export interface Source {
	addEventListener(
		type: string,
		callback: EventListenerOrEventListenerObject | null,
		options?: boolean | AddEventListenerOptions,
	): void;
	close(): void;
	onmessage: ((event: MessageEvent) => void) | null;
}

/** What a source is made by. */
export type SourceFactory = new (url: string) => Source;

/** What the connection needs: the browser's own, unless a test says otherwise. */
export interface LiveOptions {
	/** The browser's `EventSource`. */
	readonly source?: SourceFactory;
	/** What the jitter is made of (0 to 1, as `Math.random`). */
	readonly random?: () => number;
	readonly timers?: Timers;
	/** What to do when a stream is back after a break, or says it fell behind. */
	readonly reload?: () => void;
	/** What "now" is, for an event the page stamps itself. */
	readonly now?: () => string;
}

/** One stream the page follows, and where it is. */
interface Stream {
	readonly path: string;
	readonly names: readonly string[];
	readonly received: (name: string, data: unknown) => void;
	source: Source | null;
	timer: ReturnType<typeof setTimeout> | null;
	/** How many times in a row this stream failed to open (0: it is up). */
	attempt: number;
	/** Whether it was ever up: a second open is a reconnect, and the page reloads. */
	wasLive: boolean;
}

export class LiveConnection {
	readonly #source: SourceFactory;
	readonly #random: () => number;
	readonly #timers: Timers;
	readonly #reload: () => void;
	readonly #streams = new Map<string, Stream>();

	constructor(options: LiveOptions = {}) {
		this.#source = options.source ?? EventSource;
		this.#random = options.random ?? Math.random;
		this.#timers = options.timers ?? REAL_TIMERS;
		this.#reload = options.reload ?? (() => {});
	}

	/**
	 * Follow `path`, handing each event to `received` — the events named in `names`, and any
	 * unnamed one (a download's stream names its own, so it lists them).
	 */
	watch(
		path: string,
		names: readonly string[],
		received: (name: string, data: unknown) => void,
	): void {
		if (this.#streams.has(path)) {
			return;
		}
		this.#streams.set(path, {
			path,
			names,
			received,
			source: null,
			timer: null,
			attempt: 0,
			wasLive: false,
		});
		this.#connect(path);
	}

	/** Stop following `path`, if the page was. */
	stop(path: string): void {
		const stream = this.#streams.get(path);
		if (stream === undefined) {
			return;
		}
		this.#streams.delete(path);
		if (stream.timer !== null) {
			this.#timers.cancel(stream.timer);
		}
		stream.source?.close();
	}

	/** Stop following everything (the page signed out). */
	close(): void {
		for (const path of [...this.#streams.keys()]) {
			this.stop(path);
		}
	}

	/** Open (or reopen) `path`'s stream. */
	#connect(path: string): void {
		const stream = this.#streams.get(path);
		if (stream === undefined) {
			return;
		}
		const source = new this.#source(path);
		stream.source = source;
		source.addEventListener("open", () => this.#opened(stream));
		source.addEventListener("error", () => this.#lost(stream));
		for (const name of stream.names) {
			source.addEventListener(name, (event) => this.#received(stream, name, event));
		}
		source.onmessage = (event) => this.#received(stream, "message", event);
	}

	#opened(stream: Stream): void {
		stream.attempt = 0;
		const again = stream.wasLive;
		stream.wasLive = true;
		if (stream.path === EVENTS_STREAM) {
			setConnection("live");
		}
		if (again) {
			// (A stream is live only: what it missed while it was away has to be asked for again.)
			this.#reload();
		}
	}

	#lost(stream: Stream): void {
		stream.source?.close();
		stream.source = null;
		if (stream.path === EVENTS_STREAM) {
			setConnection("connecting");
		}
		const base = Math.min(MAX_DELAY, FIRST_DELAY * 2 ** stream.attempt);
		stream.attempt += 1;
		// (Half to one and a half times the base: every tab of every reader does not retry at once.)
		const delay = Math.round(base * (0.5 + this.#random()));
		stream.timer = this.#timers.after(() => {
			stream.timer = null;
			this.#connect(stream.path);
		}, delay);
	}

	#received(stream: Stream, name: string, event: Event): void {
		stream.received(name, parse(event instanceof MessageEvent ? event.data : null));
	}
}

/** An event's data: the JSON udl sent, or the text when it is not JSON. */
function parse(data: unknown): unknown {
	if (typeof data !== "string") {
		return data;
	}
	try {
		return JSON.parse(data);
	} catch {
		return data;
	}
}

/** The id of the job an event is about (a `JobEvent` names it as `job.id`). */
function jobIdOf(data: unknown): string | null {
	if (typeof data === "object" && data !== null && "job" in data) {
		const job = data.job;
		if (typeof job === "object" && job !== null && "id" in job && typeof job.id === "string") {
			return job.id;
		}
	}
	return null;
}

/** The id of a run, when what came is one (udl sends its models' JSON). */
function idOf(data: unknown): string | null {
	if (typeof data === "object" && data !== null && "id" in data && typeof data.id === "string") {
		return data.id;
	}
	return null;
}

/** The page's live connection: what to stop, and a download's stream to follow. */
export interface Live {
	/** Stop following everything (the page signed out). */
	readonly stop: () => void;
	/** Follow a job's events (a job's id, not a batch's), until the job has ended. */
	readonly download: (id: string) => void;
}

/** Follow udl's streams for the page, feeding the `live` store; returns the way to stop. */
export function startLive(options: LiveOptions = {}): Live {
	const now = options.now ?? (() => new Date().toISOString());
	const connection = new LiveConnection(options);

	setConnection("connecting");
	connection.watch(EVENTS_STREAM, EVENT_TYPES, (name, data) => {
		if (name === "event") {
			pushEvent({ name, at: now(), data });
			return;
		}
		if (name === "run") {
			const id = idOf(data);
			if (id !== null) {
				setRun(id, data);
			}
			// (A run's state is a list's data too: the page asks its loaders again.)
			connectionReload(options);
			return;
		}
		// (Lagged: this client fell behind, so what it shows may be missing changes.)
		connectionReload(options);
	});

	return {
		stop: () => {
			connection.close();
			clear(); // (a signed-out page's events, runs and jobs are not the next session's)
		},
		download: (id: string) => {
			// (`id` is a job's. udl's stream takes a batch's id too, and it ends only once every
			// job of the batch has ended: a page that follows a batch counts its jobs itself.)
			const path = `/downloads/${id}/events`;
			connection.watch(path, JOB_TYPES, (name, data) => {
				setJob(id, { name, at: now(), data });
				if (JOB_ENDS.includes(name) && jobIdOf(data) === id) {
					// (The job is over and the server closed the stream: reconnecting to it would
					// be a request a second for a download that is finished.)
					connection.stop(path);
				}
			});
		},
	};
}

/** Ask the page to reload its lists (what the connection was given, if anything). */
function connectionReload(options: LiveOptions): void {
	options.reload?.();
}
