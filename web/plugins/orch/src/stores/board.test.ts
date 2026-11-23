import type { JsonValue } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { boardStore } from "./board";

/** What the four calls the board reads answer, as the plugin reads them. */
const ANSWERS: Record<string, unknown> = {
	orch_list_features: {
		project: "udl",
		features: [{ key: "29", title: "Screens and the orch UI plugin", status: "active" }],
	},
	orch_list_tasks: {
		project: "udl",
		tasks: [
			{
				key: "29.2",
				feature: "29",
				title: "The UI plugin's skeleton",
				state: "in_progress",
				size: "L",
			},
		],
	},
	orch_list_agents: {
		items: [
			{
				agent: { name: "deepseek-a", title: "deepseek-a", role: "implementer" },
				status: "working",
				queue: [],
				reports: [],
			},
		],
	},
	orch_merge_list: {
		project: "udl",
		auto_merge: true,
		merger: "claude",
		ready: [
			{
				task: "29.2",
				feature: "29",
				title: "The UI plugin's skeleton",
				number: 80,
				url: "https://x/80",
			},
		],
		coming: [],
		held: [],
		done: [],
	},
	orch_progress: {
		project: "udl",
		features: [{ key: "29", title: "Screens", points: 26, merged: 8 }],
		models: [{ model: "m-1", runner: "claude-code", tasks: 9, hours_per_point: { hours: 1.5 } }],
	},
	orch_suggest: {
		project: "udl",
		suggestions: [
			{
				task: "29.3",
				title: "Inbox",
				feature: "29",
				agent: "deepseek-a",
				score: 3,
				reasons: ["it is free"],
			},
		],
	},
};

describe("the board store", () => {
	it("reads the features first, then the rest of the board for that project", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);

		const { store, stop } = boardStore(fake.ctx);
		await settle();

		expect(fake.calls[0]?.name).toBe("orch_list_features");
		expect(fake.calls.map((one) => one.name).sort()).toEqual([
			"orch_list_agents",
			"orch_list_features",
			"orch_list_tasks",
			"orch_merge_list",
			"orch_progress",
			"orch_suggest",
		]);
		// (The project the features named is what the rest are asked for.)
		for (const call of fake.calls.slice(1)) {
			expect(call.request.project).toBe("udl");
		}
		const state = store.getState();
		expect(state.features.map((one) => one.key)).toEqual(["29"]);
		expect(state.tasks.map((one) => one.key)).toEqual(["29.2"]);
		expect(state.agents.map((one) => one.name)).toEqual(["deepseek-a"]);
		expect(state.suggestions.map((one) => one.task)).toEqual(["29.3"]);
		expect(state.project).toBe("udl");
		expect(state.merge.ready[0]?.url).toBe("https://x/80");
		expect(state.progress.features[0]?.merged).toBe(8);
		expect(state.progress.models[0]?.hours_per_point).toBe(1.5);
		expect(state.error).toBeNull();
		stop();
	});

	it("reads it again when a pull is readied, and when a session starts or ends", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const { stop } = boardStore(fake.ctx);
		await settle();

		fake.emit("orch.task.ready", { task: "29.2" });
		await settle();
		fake.emit("orch.session.ended", { agent: "deepseek-a" });
		await settle();

		expect(fake.calls).toHaveLength(18);
		stop();
	});

	it("reads once for a burst of events, not once each", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const { stop } = boardStore(fake.ctx);
		await settle();

		// (A runner streams a line at a time: the board is not asked to read for each one.)
		for (let line = 0; line < 20; line += 1) {
			fake.emit("orch.session.started", { agent: "deepseek-a" });
		}
		await settle();

		// (Twenty lines published together are one read: 12 calls, not 20 × 6.)
		expect(fake.calls).toHaveLength(12);
		stop();
	});

	it("says what a refetch failed with, and keeps what it drew", async () => {
		let fail = false;
		const fake = fakeCtx((name) => {
			if (fail) {
				throw new Error("udl is not answering");
			}
			return ANSWERS[name] as never;
		});
		const { store, stop } = boardStore(fake.ctx);
		await settle();

		fail = true;
		fake.emit("orch.task.ready", { task: "29.2" });
		await settle();

		expect(store.getState().error).toBe("udl is not answering");
		expect(store.getState().tasks.map((one) => one.key)).toEqual(["29.2"]);
		stop();
	});

	it("ignores events that are not the board's", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const { stop } = boardStore(fake.ctx);
		await settle();

		fake.emit("github.pull.updated");
		fake.emit("orch.session.event");
		await settle();

		expect(fake.calls).toHaveLength(6);
		stop();
	});

	it("changes nothing when a read finishes after it was stopped", async () => {
		let answer: (value: JsonValue) => void = () => {};
		const shared = new Promise<JsonValue>((done) => {
			answer = done;
		});
		const fake = fakeCtx(() => shared);
		const { store, stop } = boardStore(fake.ctx);

		stop();
		answer({ features: [{ key: "29" }] });
		await settle();

		expect(store.getState().features).toEqual([]);
	});

	it("says nothing when a read fails after it was stopped", async () => {
		let fail: (why: unknown) => void = () => {};
		const fake = fakeCtx(
			() =>
				new Promise((_done, refuse) => {
					fail = refuse;
				}),
		);
		const { store, stop } = boardStore(fake.ctx);

		stop();
		fail(new Error("too late"));
		await settle();

		expect(store.getState().error).toBeNull();
	});

	it("says what failed", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no");
		});
		const { store, stop } = boardStore(fake.ctx);
		await settle();

		expect(store.getState().error).toBe("no");
		stop();
	});
});
