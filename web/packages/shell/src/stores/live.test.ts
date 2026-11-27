import { beforeEach, describe, expect, it } from "vitest";
import { clear, LIMIT, live, pushEvent, setConnection, setJob, setRun, useLive } from "./live";

beforeEach(() => {
	clear();
});

describe("the live store", () => {
	it("starts offline and empty", () => {
		expect(live.getState().connection).toBe("offline");
		expect(live.getState().events).toEqual([]);
		expect(live.getState().runs).toEqual({});
		expect(live.getState().jobs).toEqual({});
	});

	it("says where the connection is", () => {
		setConnection("connecting");
		expect(live.getState().connection).toBe("connecting");

		setConnection("live");
		expect(live.getState().connection).toBe("live");
	});

	it("keeps the newest events, and drops the oldest when it is full", () => {
		for (let id = 1; id <= LIMIT + 5; id++) {
			pushEvent({ name: "event", at: "2026-11-19T12:00:00Z", data: { n: id } });
		}

		const { events } = live.getState();

		expect(events).toHaveLength(LIMIT);
		expect(events[0]?.data).toEqual({ n: 6 });
		expect(events.at(-1)?.data).toEqual({ n: LIMIT + 5 });
	});

	it("keeps a run and a job by id, replacing what it had", () => {
		setRun("run-1", { state: "running" });
		setRun("run-1", { state: "done" });
		setJob("job-1", { status: "downloading" });

		expect(live.getState().runs).toEqual({ "run-1": { state: "done" } });
		expect(live.getState().jobs).toEqual({ "job-1": { status: "downloading" } });
	});

	it("forgets everything a connection brought in", () => {
		setConnection("live");
		pushEvent({ name: "x", at: "2026-11-19T12:00:00Z", data: null });
		setRun("run-1", {});

		clear();

		expect(live.getState().connection).toBe("offline");
		expect(live.getState().events).toEqual([]);
		expect(live.getState().runs).toEqual({});
	});

	it("hands a piece of the state to a component", () => {
		setConnection("live");

		const seen: string[] = [];
		const unsubscribe = live.subscribe((state) => seen.push(state.connection));
		setConnection("offline");
		unsubscribe();

		expect(seen).toEqual(["offline"]);
		expect(useLive).toBeTypeOf("function");
	});

	it("keeps an event once, as its latest copy: stored, then dispatched (Q: 18.6-dupe)", () => {
		// (18.6's stream sends an event twice by design: when it is stored, and when it is
		// dispatched. The timeline is of events, not of notices.)
		pushEvent({
			name: "event",
			at: "2026-11-28T01:00:00Z",
			data: { id: "e-1", type: "custom.hello", status: "pending" },
		});
		pushEvent({
			name: "event",
			at: "2026-11-28T01:00:01Z",
			data: { id: "e-1", type: "custom.hello", status: "dispatched" },
		});
		pushEvent({
			name: "event",
			at: "2026-11-28T01:00:02Z",
			data: { id: "e-2", type: "custom.bye", status: "pending" },
		});

		const { events } = live.getState();

		expect(events).toHaveLength(2);
		expect(events[0]?.data).toMatchObject({ id: "e-1", status: "dispatched" });
		expect(events[1]?.data).toMatchObject({ id: "e-2" });
	});

	it("keeps a run's notices as they come: they are not one event", () => {
		pushEvent({ name: "run", at: "2026-11-28T01:00:00Z", data: { id: "r-1", status: "queued" } });
		pushEvent({ name: "run", at: "2026-11-28T01:00:01Z", data: { id: "r-1", status: "done" } });

		expect(live.getState().events).toHaveLength(2);
	});
});
