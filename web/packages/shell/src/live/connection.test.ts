import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clear, live } from "../stores/live";
import {
	EVENTS_STREAM,
	FIRST_DELAY,
	LiveConnection,
	REAL_TIMERS,
	startLive,
	type Timers,
} from "./connection";

/** A stream as the browser's `EventSource` is one, and as a test can drive it. */
class FakeSource extends EventTarget {
	static readonly CONNECTING = 0 as const;
	static readonly OPEN = 1 as const;
	static readonly CLOSED = 2 as const;

	readonly url: string;
	readyState: number = FakeSource.CONNECTING;
	onmessage: ((event: MessageEvent) => void) | null = null;
	closed = false;

	constructor(url: string) {
		super();
		this.url = url;
		FakeSource.made.push(this);
	}

	close(): void {
		this.closed = true;
		this.readyState = 2;
	}

	/** udl accepted the stream. */
	open(): void {
		this.readyState = 1;
		this.dispatchEvent(new Event("open"));
	}

	/** The stream broke (the browser would now retry on its own; this one does not). */
	fail(): void {
		this.readyState = 2;
		this.dispatchEvent(new Event("error"));
	}

	/** udl sent an event: one with a name, or (no name) a plain `message`. */
	send(name: string, data: unknown): void {
		const event = new MessageEvent(name === "message" ? "message" : name, {
			data: typeof data === "string" ? data : JSON.stringify(data),
		});
		if (name === "message") {
			this.onmessage?.(event);
		} else {
			this.dispatchEvent(event);
		}
	}

	/** Every source a test has made, oldest first. */
	static made: FakeSource[] = [];
}

/** Timers a test drives by hand: what was asked for, and when. */
function fakeTimers(): Timers & { waits: number[]; fire: () => void } {
	const waiting: { run: () => void; ms: number }[] = [];
	const waits: number[] = [];
	return {
		waits,
		after: (run, ms) => {
			waiting.push({ run, ms });
			waits.push(ms);
			return 0 as unknown as ReturnType<typeof setTimeout>;
		},
		cancel: () => {},
		fire: () => {
			waiting.shift()?.run();
		},
	};
}

beforeEach(() => {
	FakeSource.made = [];
	clear();
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe("the live connection", () => {
	it("opens the stream it is told to, and says it is live", () => {
		const timers = fakeTimers();
		const connection = new LiveConnection({ source: FakeSource, timers });

		connection.watch(EVENTS_STREAM, ["event"], () => {});

		expect(FakeSource.made).toHaveLength(1);
		expect(FakeSource.made[0]?.url).toBe(EVENTS_STREAM);

		FakeSource.made[0]?.open();

		expect(live.getState().connection).toBe("live");
	});

	it("follows a stream only once, however often it is asked to", () => {
		const connection = new LiveConnection({ source: FakeSource, timers: fakeTimers() });

		connection.watch(EVENTS_STREAM, ["event"], () => {});
		connection.watch(EVENTS_STREAM, ["event"], () => {});

		expect(FakeSource.made).toHaveLength(1);
	});

	it("hands each named event its data, as the JSON udl sent", () => {
		const seen: [string, unknown][] = [];
		const connection = new LiveConnection({ source: FakeSource, timers: fakeTimers() });
		connection.watch(EVENTS_STREAM, ["event", "run"], (name, data) => seen.push([name, data]));

		FakeSource.made[0]?.send("event", { id: "e-1", type: "download.finished" });
		FakeSource.made[0]?.send("run", "not JSON");

		expect(seen).toEqual([
			["event", { id: "e-1", type: "download.finished" }],
			["run", "not JSON"],
		]);
	});

	it("waits 1 s, then doubles, and never longer than 30 s", () => {
		const waits: number[] = [];
		const timers: Timers = {
			after: (_run, ms) => {
				waits.push(ms);
				return 0 as unknown as ReturnType<typeof setTimeout>;
			},
			cancel: () => {},
		};
		const connection = new LiveConnection({ source: FakeSource, timers, random: () => 0.5 });

		connection.watch(EVENTS_STREAM, ["event"], () => {});
		for (let i = 0; i < 8; i++) {
			FakeSource.made.at(-1)?.fail();
		}

		expect(waits).toEqual([1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000]);
	});

	it("spreads the retries with jitter, from half to one and a half times the wait", () => {
		const waits: number[] = [];
		const timers: Timers = {
			after: (_run, ms) => {
				waits.push(ms);
				return 0 as unknown as ReturnType<typeof setTimeout>;
			},
			cancel: () => {},
		};
		const random = vi.fn(() => 0);
		const connection = new LiveConnection({ source: FakeSource, timers, random });

		connection.watch(EVENTS_STREAM, ["event"], () => {});
		FakeSource.made.at(-1)?.fail();
		FakeSource.made.at(-1)?.fail();

		expect(waits).toEqual([FIRST_DELAY / 2, FIRST_DELAY]);
		expect(random).toHaveBeenCalledTimes(2);
	});

	it("reloads the page's lists when a stream is back after a break, and not the first time", () => {
		const reload = vi.fn();
		const timers = fakeTimers();
		const connection = new LiveConnection({ source: FakeSource, timers, reload });
		connection.watch(EVENTS_STREAM, ["event"], () => {});

		FakeSource.made[0]?.open();
		expect(reload).not.toHaveBeenCalled();

		FakeSource.made[0]?.fail();
		expect(live.getState().connection).toBe("connecting");
		timers.fire();
		FakeSource.made[1]?.open();

		expect(reload).toHaveBeenCalledOnce();
	});

	it("starts again at 1 s once it is back, so a long drop does not leave it waiting long", () => {
		const timers = fakeTimers();
		const connection = new LiveConnection({ source: FakeSource, timers, random: () => 0.5 });
		connection.watch(EVENTS_STREAM, ["event"], () => {});

		FakeSource.made[0]?.fail();
		FakeSource.made[0]?.fail();
		timers.fire();
		FakeSource.made[1]?.open();
		FakeSource.made[1]?.fail();

		expect(timers.waits).toEqual([1_000, 2_000, 1_000]);
	});

	it("stops the stream, and waits for nothing more, when the page is done", () => {
		const timers = fakeTimers();
		const connection = new LiveConnection({ source: FakeSource, timers });
		connection.watch(EVENTS_STREAM, ["event"], () => {});

		connection.close();

		expect(FakeSource.made[0]?.closed).toBe(true);
	});

	it("stops a stream it does not follow as nothing", () => {
		const connection = new LiveConnection({ source: FakeSource, timers: fakeTimers() });

		connection.stop("/nope");

		expect(FakeSource.made).toHaveLength(0);
	});

	it("keeps the last 500 events, and a run and a job by id", () => {
		const live500 = startLive({ source: FakeSource, timers: fakeTimers(), now: () => "12:00" });
		const source = FakeSource.made[0];
		source?.open();

		for (let i = 0; i < 505; i++) {
			source?.send("event", { id: `e-${i}` });
		}
		source?.send("run", { id: "run-1", state: "running" });
		live500.download("job-1");
		FakeSource.made[1]?.send("progress", { percent: 40 });

		const state = live.getState();
		expect(state.events).toHaveLength(500);
		expect(state.events[0]?.data).toEqual({ id: "e-5" });
		expect(state.events[0]?.at).toBe("12:00");
		expect(state.runs).toEqual({ "run-1": { id: "run-1", state: "running" } });
		expect(state.jobs).toEqual({
			"job-1": { name: "progress", at: "12:00", data: { percent: 40 } },
		});
	});

	it("reloads when udl says this client fell behind", () => {
		const reload = vi.fn();
		const live500 = startLive({ source: FakeSource, timers: fakeTimers(), reload });
		FakeSource.made[0]?.open();

		FakeSource.made[0]?.send("lagged", { lagged: true });

		expect(reload).toHaveBeenCalledOnce();
		expect(live500.stop).toBeTypeOf("function");
	});

	it("reloads when a run changes, and keeps a run that came without an id out of the store", () => {
		const reload = vi.fn();
		startLive({ source: FakeSource, timers: fakeTimers(), reload });
		FakeSource.made[0]?.open();

		FakeSource.made[0]?.send("run", { state: "running" });

		expect(reload).toHaveBeenCalledOnce();
		expect(live.getState().runs).toEqual({});
	});

	it("forgets everything it was given when the page stops following", () => {
		// (A signed-out page's data is not kept: the next session must not see the last one's.)
		const live500 = startLive({ source: FakeSource, timers: fakeTimers() });
		FakeSource.made[0]?.open();
		FakeSource.made[0]?.send("event", { id: "e-1" });
		FakeSource.made[0]?.send("run", { id: "run-1", state: "running" });
		live500.download("job-1");
		FakeSource.made[1]?.send("progress", { percent: 40 });

		live500.stop();

		expect(live.getState()).toEqual({
			connection: "offline",
			events: [],
			runs: {},
			jobs: {},
		});
	});

	it("stops following a download once its job has ended", () => {
		// (A download's stream is over when the job is: reconnecting to it forever would be a
		// request a second, and a reload with it.)
		const timers = fakeTimers();
		const reload = vi.fn();
		const live500 = startLive({ source: FakeSource, timers, reload });
		FakeSource.made[0]?.open();
		live500.download("job-1");
		const stream = FakeSource.made[1];
		stream?.open();

		stream?.send("finished", { id: "job-1" });
		stream?.fail();
		timers.fire();

		expect(FakeSource.made).toHaveLength(2); // (no third source: it did not reconnect)
		expect(reload).not.toHaveBeenCalled();
		expect(live.getState().jobs["job-1"]).toMatchObject({ name: "finished" });
	});

	it("keeps following a download while its job is running", () => {
		const timers = fakeTimers();
		const live500 = startLive({ source: FakeSource, timers });
		FakeSource.made[0]?.open();
		live500.download("job-1");
		const stream = FakeSource.made[1];
		stream?.open();

		stream?.send("progress", { percent: 10 });
		stream?.fail();
		timers.fire();

		expect(FakeSource.made).toHaveLength(3); // (the first, the job's, and the one it reopened)
	});

	it("says connecting before its first event, and lives on the page's own clock", () => {
		startLive({ source: FakeSource });

		expect(live.getState().connection).toBe("connecting");
		expect(REAL_TIMERS.after).toBeTypeOf("function");
		REAL_TIMERS.cancel(REAL_TIMERS.after(() => {}, 1));
	});
});
