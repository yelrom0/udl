import type { JsonValue } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { sessionsStore } from "./sessions";

/** What `orch_get_session` answers, as the plugin reads it. */
const SESSION = {
	id: "ses_1",
	agent: "deepseek-a",
	task: "29.2",
	state: "working",
	turns: 3,
	last_text: "Reading the shell.",
};

describe("the sessions store", () => {
	it("reads a session when one is asked for", async () => {
		const fake = fakeCtx(() => SESSION);

		const { store, read, stop } = sessionsStore(fake.ctx);
		await read("ses_1");

		expect(fake.calls).toEqual([{ name: "orch_get_session", request: { session: "ses_1" } }]);
		expect(store.getState().sessions.ses_1?.state).toBe("working");
		expect(store.getState().error).toBeNull();
		stop();
	});

	it("reads it again when that session says something", async () => {
		const fake = fakeCtx(() => SESSION);
		const { read, stop } = sessionsStore(fake.ctx);
		await read("ses_1");

		fake.emit("orch.session.event", { session: "ses_1", agent: "deepseek-a" });
		await settle();
		fake.emit("orch.session.event", { session: "ses_2", agent: "deepseek-b" });
		await settle();

		expect(fake.calls).toHaveLength(2);
		stop();
	});

	it("reads every session it watches when an event names none", async () => {
		const fake = fakeCtx(() => SESSION);
		const { read, stop } = sessionsStore(fake.ctx);
		await read("ses_1");
		await read("ses_2");

		fake.emit("orch.session.ended", {});
		await settle();

		expect(fake.calls).toHaveLength(4);
		stop();
	});

	it("stops listening when it is stopped", async () => {
		const fake = fakeCtx(() => SESSION);
		const { read, stop } = sessionsStore(fake.ctx);
		await read("ses_1");

		stop();
		fake.emit("orch.session.event", { session: "ses_1" });
		await settle();

		expect(fake.calls).toHaveLength(1);
		expect(fake.listeners()).toBe(0);
	});

	it("changes nothing when a read finishes after it was stopped", async () => {
		let answer: (value: JsonValue) => void = () => {};
		const fake = fakeCtx(
			() =>
				new Promise((done) => {
					answer = done;
				}),
		);
		const { store, read, stop } = sessionsStore(fake.ctx);
		const reading = read("ses_1");

		stop();
		answer(SESSION);
		await reading;

		expect(store.getState().sessions).toEqual({});
	});

	it("says nothing when a read fails after it was stopped", async () => {
		let fail: (why: unknown) => void = () => {};
		const fake = fakeCtx(
			() =>
				new Promise((_done, refuse) => {
					fail = refuse;
				}),
		);
		const { store, read, stop } = sessionsStore(fake.ctx);
		const reading = read("ses_1");

		stop();
		fail(new Error("too late"));
		await reading;

		expect(store.getState().error).toBeNull();
	});

	it("says what failed", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no such session");
		});
		const { store, read, stop } = sessionsStore(fake.ctx);
		await read("ses_9");

		expect(store.getState().error).toBe("no such session");
		stop();
	});
});
