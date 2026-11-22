import type { JsonValue } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { inboxStore } from "./inbox";

/** What `orch_inbox` answers, as the plugin reads it. */
const INBOX = {
	project: "udl",
	merge: {
		project: "udl",
		auto_merge: true,
		merger: "claude",
		ready: [
			{ task: "29.2", feature: "29", title: "The UI plugin's skeleton", number: 78, url: "" },
		],
		coming: [],
		held: [],
		done: [],
	},
	questions: [
		{
			key: "Q-8",
			text: "Should a task keep its worktree?",
			to: "humans",
			state: "open",
			options: [],
		},
	],
	actions: [{ key: "A-1", title: "Buy a domain", when: "now" }],
	agents: [
		{
			name: "deepseek-a",
			title: "deepseek-a",
			role: "implementer",
			state: "working",
			line: "deepseek-a — implementing 29.2",
		},
	],
	updated_at: "2026-11-23T00:00:00Z",
};

describe("the inbox store", () => {
	it("reads the inbox once it is made", async () => {
		const fake = fakeCtx(() => INBOX);

		const { store, stop } = inboxStore(fake.ctx);
		await settle();

		expect(fake.calls.map((one) => one.name)).toEqual(["orch_inbox"]);
		expect(store.getState().inbox?.project).toBe("udl");
		expect(store.getState().error).toBeNull();
		stop();
	});

	it("reads it again when an orch event arrives", async () => {
		const fake = fakeCtx(() => INBOX);
		const { stop } = inboxStore(fake.ctx);
		await settle();

		fake.emit("orch.task.ready");
		await settle();

		expect(fake.calls.map((one) => one.name)).toEqual(["orch_inbox", "orch_inbox"]);
		stop();
	});

	it("ignores events that are not orch's", async () => {
		const fake = fakeCtx(() => INBOX);
		const { stop } = inboxStore(fake.ctx);
		await settle();

		fake.emit("github.pull.updated");
		await settle();

		expect(fake.calls).toHaveLength(1);
		stop();
	});

	it("stops listening when it is stopped", async () => {
		const fake = fakeCtx(() => INBOX);
		const { stop } = inboxStore(fake.ctx);
		await settle();

		stop();
		fake.emit("orch.task.ready");
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
		const { store, stop } = inboxStore(fake.ctx);

		stop();
		answer(INBOX);
		await settle();

		expect(store.getState().inbox).toBeNull();
	});

	it("says nothing when a read fails after it was stopped", async () => {
		let fail: (why: unknown) => void = () => {};
		const fake = fakeCtx(
			() =>
				new Promise((_done, refuse) => {
					fail = refuse;
				}),
		);
		const { store, stop } = inboxStore(fake.ctx);

		stop();
		fail(new Error("too late"));
		await settle();

		expect(store.getState().error).toBeNull();
	});

	it("says what a rejection says when it is not an error", async () => {
		const fake = fakeCtx(() => {
			throw "udl is away";
		});
		const { store, stop } = inboxStore(fake.ctx);
		await settle();

		expect(store.getState().error).toBe("udl is away");
		stop();
	});

	it("says what failed, and keeps what it had", async () => {
		let fail = false;
		const fake = fakeCtx(() => {
			if (fail) {
				throw new Error("udl is not answering");
			}
			return INBOX;
		});
		const { store, stop } = inboxStore(fake.ctx);
		await settle();

		fail = true;
		fake.emit("orch.task.ready");
		await settle();

		expect(store.getState().error).toBe("udl is not answering");
		expect(store.getState().inbox?.project).toBe("udl");
		stop();
	});
});
