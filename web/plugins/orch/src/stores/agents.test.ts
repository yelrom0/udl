import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { agentStore, agentsStore } from "./agents";

/** What the calls an agents' page reads answer. */
const ANSWERS: Record<string, unknown> = {
	orch_list_agents: {
		items: [
			{
				agent: {
					name: "boss",
					title: "Boss",
					role: "manager",
					manager: null,
					runner: "opencode",
					model: "deepseek-v4.1",
				},
				status: "idle",
				reports: ["deepseek-a"],
			},
			{
				agent: {
					name: "deepseek-a",
					title: "DeepSeek A",
					role: "implementer",
					manager: "boss",
					runner: "opencode",
					model: "deepseek-v4.1",
				},
				status: "working",
				session: { id: "s-1", agent: "deepseek-a", task: "29.6", state: "running" },
				queue: [
					{ key: "29.6", project: "udl", feature: "29", title: "The agents", state: "in_progress" },
				],
				reports: [],
			},
		],
	},
	orch_list_runners: {
		items: [
			{
				host: "local",
				runner: "opencode",
				health: { installed: true, signed_in: true, version: "1.2.3" },
				models: [{ id: "deepseek-v4.1", name: "DeepSeek V4.1" }],
			},
		],
		problems: [],
	},
	orch_get_agent: {
		agent: {
			name: "deepseek-a",
			title: "DeepSeek A",
			role: "implementer",
			manager: "boss",
			runner: "opencode",
			model: "deepseek-v4.1",
			host: "local",
			manages: {},
			projects: ["udl"],
		},
		status: "working",
		session: {
			id: "0b9a1f8e-0000-4000-8000-000000000001",
			agent: "deepseek-a",
			task: "29.6",
			ref: { runner: "opencode", id: "ses-1", cwd: "/work", host: "local" },
			state: "running",
			turns: 2,
			tokens_in: 900,
			tokens_out: 120,
			cost: 0.2,
			last_text: "Working.",
		},
		queue: [
			{ key: "29.6", project: "udl", feature: "29", title: "The agents", state: "in_progress" },
			{ key: "29.12", project: "udl", feature: "29", title: "The chat", state: "queued" },
		],
		reports: [],
	},
	orch_list_tasks: {
		project: "udl",
		tasks: [
			{ key: "29.6", feature: "29", title: "The agents", state: "in_progress", size: "L" },
			{ key: "29.12", feature: "29", title: "The chat", state: "queued", size: "M" },
		],
	},
	orch_progress: {
		features: [],
		models: [{ model: "deepseek-v4.1", hours_per_point: { hours: 0.5, source: "model" } }],
	},
	orch_merge_list: { project: "udl", ready: [], coming: [], held: [], done: [] },
};

describe("the agents' store (29.6)", () => {
	it("reads every agent, and says a read came back", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = agentsStore(fake.ctx);
		await settle();

		const state = loaded.store.getState();
		expect(state.items.map((one) => one.agent.name)).toEqual(["boss", "deepseek-a"]);
		expect(state.items[1]?.agent.manager).toBe("boss");
		expect(state.loaded).toBe(true);
		expect(state.error).toBeNull();
	});

	it("reads the harnesses nowhere: an editor asks for its own", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		agentsStore(fake.ctx);
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_list_runners")).toEqual([]);
	});

	it("says what went wrong when the agents cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("it is down");
		});
		const loaded = agentsStore(fake.ctx);
		await settle();

		expect(loaded.store.getState().error).toBe("it is down");
		expect(loaded.store.getState().items).toEqual([]);
		// (A read that failed is not a page still reading: it says what went wrong.)
		expect(loaded.store.getState().loaded).toBe(true);
	});

	it("reads again when an agent or a session moves, and stops when the page does", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = agentsStore(fake.ctx);
		await settle();

		fake.emit("orch.session.ended", { agent: "deepseek-a" });
		await settle();
		expect(fake.calls.filter((one) => one.name === "orch_list_agents")).toHaveLength(2);

		// (An agent gone idle or out of quota is what the page's status shows.)
		fake.emit("orch.agent.idle", { agent: "deepseek-a" });
		await settle();
		expect(fake.calls.filter((one) => one.name === "orch_list_agents")).toHaveLength(3);

		// (And another tab's edit is this page's news: the tree reads again.)
		fake.emit("orch.agent.changed", { agent: "boss" });
		await settle();
		expect(fake.calls.filter((one) => one.name === "orch_list_agents")).toHaveLength(4);

		loaded.stop();
		fake.emit("orch.agent.limited", { agent: "boss" });
		await settle();
		expect(fake.calls.filter((one) => one.name === "orch_list_agents")).toHaveLength(4);
		expect(fake.listeners()).toBe(0);
	});
});

describe("an agent's store (29.6)", () => {
	it("reads the agent, its queue's tasks, its project's rates and the merge list", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = agentStore(fake.ctx, "deepseek-a");
		await settle();

		const state = loaded.store.getState();
		expect(state.info?.agent.name).toBe("deepseek-a");
		expect(state.info?.status).toBe("working");
		expect(state.info?.session?.id).toBe("0b9a1f8e-0000-4000-8000-000000000001");
		expect(state.tasks.map((one) => one.key)).toEqual(["29.6", "29.12"]);
		expect(state.progress.models).toEqual([{ model: "deepseek-v4.1", hours_per_point: 0.5 }]);
		expect(state.merge.project).toBe("udl");
		expect(state.loaded).toBe(true);
		// (Its chain is the whole tree's, not just its own row.)
		expect(state.agents.map((one) => one.agent.name)).toEqual(["boss", "deepseek-a"]);
		// (The projects are the queue's own: a key is only unique within one.)
		expect(
			fake.calls.filter((one) => one.name === "orch_list_tasks").map((one) => one.request),
		).toEqual([{ project: "udl" }]);
	});

	it("reads nothing beside an empty queue: there is nothing to size or merge", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_agent") {
				return { agent: { name: "idle" }, status: "idle", queue: [], reports: [] } as never;
			}
			return ANSWERS[name] as never;
		});
		agentStore(fake.ctx, "idle");
		await settle();

		expect(fake.calls.map((one) => one.name)).toEqual(["orch_get_agent", "orch_list_agents"]);
	});

	it("stands when a read beside the agent refuses", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_progress") {
				throw new Error("no GitHub account");
			}
			return ANSWERS[name] as never;
		});
		const loaded = agentStore(fake.ctx, "deepseek-a");
		await settle();

		const state = loaded.store.getState();
		expect(state.info?.agent.name).toBe("deepseek-a");
		expect(state.progress.models).toEqual([]);
		expect(state.error).toBeNull();
	});

	it("says what went wrong when the agent cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no such agent");
		});
		const loaded = agentStore(fake.ctx, "nobody");
		await settle();

		expect(loaded.store.getState().error).toBe("no such agent");
		expect(loaded.store.getState().info).toBeNull();
	});

	it("reads again when its session starts or ends, and stops when the page does", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = agentStore(fake.ctx, "deepseek-a");
		await settle();

		fake.emit("orch.session.ended", { agent: "deepseek-a" });
		await settle();
		expect(fake.calls.filter((one) => one.name === "orch_get_agent")).toHaveLength(2);

		loaded.stop();
		fake.emit("orch.task.changed", { key: "29.6" });
		await settle();
		expect(fake.calls.filter((one) => one.name === "orch_get_agent")).toHaveLength(2);
	});

	it("does not re-read everything for every line a running session says", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		agentStore(fake.ctx, "deepseek-a");
		await settle();

		// (A line is the session's own store's business: the page's card reads that store.)
		fake.emit("orch.session.event", { agent: "deepseek-a", session: "s" });
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_get_agent")).toHaveLength(1);
	});

	it("reads every project a queue spans, and stands when one of them refuses", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_agent") {
				return {
					agent: { name: "deepseek-a", role: "implementer" },
					status: "idle",
					queue: [
						{
							key: "29.6",
							project: "udl",
							feature: "29",
							title: "The agents",
							state: "in_progress",
						},
						{ key: "1.1", project: "other", feature: "1", title: "Elsewhere", state: "queued" },
					],
					reports: [],
				} as never;
			}
			if (
				name === "orch_merge_list" &&
				fake.calls.filter((one) => one.name === name).length === 2
			) {
				throw new Error("no GitHub account");
			}
			return ANSWERS[name] as never;
		});
		const loaded = agentStore(fake.ctx, "deepseek-a");
		await settle();

		expect(
			fake.calls.filter((one) => one.name === "orch_list_tasks").map((one) => one.request),
		).toEqual([{ project: "udl" }, { project: "other" }]);
		expect(loaded.store.getState().error).toBeNull();
	});
});
