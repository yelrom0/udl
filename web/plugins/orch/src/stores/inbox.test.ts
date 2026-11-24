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
		// (And the drawer's question count (29.7's follow-up): what is still open.)
		expect(fake.navCounts).toContainEqual({ route: "questions", count: INBOX.questions.length });
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

	it.each([
		["orch.question.asked", { question: "Q-8" }],
		["orch.question.answered", { question: "Q-8" }],
		["orch.action.needed", { action: "A-2" }],
		["orch.agent.idle", { agent: "claude" }],
	])("reads it again when %s arrives (28.5)", async (event, payload) => {
		// (A blocking question an agent asks, and a to-action a person must do, reach the inbox
		// because they are published — not because something else happened to make it read.)
		const fake = fakeCtx(() => INBOX);
		const { stop } = inboxStore(fake.ctx);
		await settle();

		fake.emit(event, payload);
		await settle();

		expect(fake.calls.map((one) => one.name)).toEqual(["orch_inbox", "orch_inbox"]);
		stop();
	});

	it("does not read again for every line a runner streams", async () => {
		// (`orch_inbox` reads GitHub: a line of a session's transcript is not what waits on a
		// person, and twenty of them are not twenty reads.)
		const fake = fakeCtx(() => INBOX);
		const { stop } = inboxStore(fake.ctx);
		await settle();

		for (let line = 0; line < 20; line += 1) {
			fake.emit("orch.session.event", { session: "ses_1" });
		}
		await settle();

		expect(fake.calls.map((one) => one.name)).toEqual(["orch_inbox"]);
		stop();
	});

	it("reads once for a burst of events, not once each", async () => {
		const fake = fakeCtx(() => INBOX);
		const { stop } = inboxStore(fake.ctx);
		await settle();

		fake.emit("orch.task.ready", { task: "29.2" });
		fake.emit("orch.task.merged", { task: "28.7" });
		fake.emit("orch.project.changed", {});
		await settle();

		// (Three events published together are one read, not three.)
		expect(fake.calls.map((one) => one.name)).toEqual(["orch_inbox", "orch_inbox"]);
		stop();
	});

	it("says what a refetch failed with, and keeps what it drew", async () => {
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
		fake.emit("orch.task.ready", { task: "29.2" });
		await settle();

		expect(store.getState().error).toBe("udl is not answering");
		expect(store.getState().inbox?.project).toBe("udl");
		stop();
	});

	it("reads again when a link is refreshed, which is how a pull's checks arrive", async () => {
		const fake = fakeCtx(() => INBOX);
		const { stop } = inboxStore(fake.ctx);
		await settle();

		fake.emit("orch.link.changed", { kind: "pull", key: "80" });
		await settle();

		expect(fake.calls.map((one) => one.name)).toEqual(["orch_inbox", "orch_inbox"]);
		stop();
	});

	it("reads again when a page asks it to, so an answer's button comes back at once", async () => {
		const fake = fakeCtx(() => INBOX);
		const { store, refresh, stop } = inboxStore(fake.ctx);
		await settle();

		await refresh();

		expect(fake.calls.map((one) => one.name)).toEqual(["orch_inbox", "orch_inbox"]);
		expect(store.getState().inbox).not.toBeNull();
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
