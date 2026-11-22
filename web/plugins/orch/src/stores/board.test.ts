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
	it("reads the features, tasks, agents and suggestions once it is made", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);

		const { store, stop } = boardStore(fake.ctx);
		await settle();

		expect(fake.calls.map((one) => one.name).sort()).toEqual([
			"orch_list_agents",
			"orch_list_features",
			"orch_list_tasks",
			"orch_suggest",
		]);
		const state = store.getState();
		expect(state.features.map((one) => one.key)).toEqual(["29"]);
		expect(state.tasks.map((one) => one.key)).toEqual(["29.2"]);
		expect(state.agents.map((one) => one.name)).toEqual(["deepseek-a"]);
		expect(state.suggestions.map((one) => one.task)).toEqual(["29.3"]);
		expect(state.error).toBeNull();
		stop();
	});

	it("reads it again when a task, a feature or an agent moves", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const { stop } = boardStore(fake.ctx);
		await settle();

		fake.emit("orch.task.moved");
		await settle();
		fake.emit("orch.agent.changed");
		await settle();

		expect(fake.calls).toHaveLength(12);
		stop();
	});

	it("ignores events that are not the board's", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const { stop } = boardStore(fake.ctx);
		await settle();

		fake.emit("github.pull.updated");
		fake.emit("orch.question.asked");
		fake.emit("orch.session.event");
		await settle();

		expect(fake.calls).toHaveLength(4);
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
