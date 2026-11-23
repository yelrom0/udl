import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { taskStore } from "./task";

/** What the calls a task's page reads answer. */
const ANSWERS: Record<string, unknown> = {
	orch_get_task: {
		feature: { key: "29", project: "udl", title: "Screens", status: "active" },
		task: {
			key: "29.2",
			feature: "29",
			title: "The skeleton",
			state: "ready",
			spec: "## It",
			size: "L",
			needs: ["28.7"],
		},
		links: [{ link_kind: "pull", url: "https://github.com/o/r/pull/80", title: "80", state: {} }],
		notes: [{ at: "2026-11-24T01:00:00Z", author: "claude", note_kind: "note", text: "ok" }],
		questions: [{ key: "Q-8", title: "Screens", state: "open", task: "29.2" }],
	},
	orch_estimate: { size: "L", estimate: "4h", worked: "1h" },
	orch_readiness: { ready: false, blockers: ["CI is red"], open_items: [], pending: ["ci"] },
	orch_list_tasks: {
		project: "udl",
		tasks: [
			{ key: "29.2", feature: "29", state: "ready", needs: [] },
			{ key: "29.5", feature: "29", state: "open", needs: ["29.2"] },
			{ key: "29.6", feature: "29", state: "open", needs: ["29.2", "29.3"] },
		],
	},
	orch_list_agents: {
		items: [
			{
				agent: { name: "deepseek-a" },
				status: "working",
				session: { agent: "deepseek-a", task: "29.2", ref: { id: "s-1" }, state: "running" },
			},
		],
	},
};

describe("a task's store (29.5)", () => {
	it("reads the task, its estimate, its readiness, what it unblocks and its session", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = taskStore(fake.ctx, "29.2");
		await settle();

		const state = loaded.store.getState();
		expect(state.info?.task.key).toBe("29.2");
		expect(state.info?.feature?.key).toBe("29");
		// (The tasks whose needs name it, and only those.)
		expect(state.info?.unblocks).toEqual(["29.5", "29.6"]);
		expect(state.estimate?.estimate).toBe("4h");
		expect(state.readiness?.blockers).toEqual(["CI is red"]);
		expect(state.session).toEqual({ agent: "deepseek-a", id: "s-1", state: "running" });
		// (The names an assignee may be, so a page links only the agents it has.)
		expect(state.agents).toEqual(["deepseek-a"]);
		loaded.stop();
	});

	it("asks for the task by its key, and the estimate and readiness for it", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = taskStore(fake.ctx, "29.2");
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_get_task")?.request).toEqual({
			key: "29.2",
		});
		// (`orch_estimate` names the task and its project; the readiness its key.)
		expect(fake.calls.find((one) => one.name === "orch_estimate")?.request).toEqual({
			project: "udl",
			task: "29.2",
		});
		expect(fake.calls.find((one) => one.name === "orch_readiness")?.request).toEqual({
			key: "29.2",
			project: "udl",
		});
		loaded.stop();
	});

	it("reads the project's tasks for what it unblocks, not only its feature's", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = taskStore(fake.ctx, "29.2");
		await settle();

		// (The project's tasks: what a task unblocks may be in another feature.)
		expect(fake.calls.find((one) => one.name === "orch_list_tasks")?.request).toEqual({
			project: "udl",
		});
		loaded.stop();
	});

	it("says what udl answered when the task cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no such task: '99.9'");
		});
		const loaded = taskStore(fake.ctx, "99.9");
		await settle();

		expect(loaded.store.getState().error).toContain("no such task");
		loaded.stop();
	});

	it("reads again when the task, its pull or a note of it moves", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = taskStore(fake.ctx, "29.2");
		await settle();
		const before = fake.calls.filter((one) => one.name === "orch_get_task").length;

		fake.emit("orch.link.changed");
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_get_task").length).toBe(before + 1);
		loaded.stop();
	});
});

describe("a task's store's reads beside the task (29.5)", () => {
	it("draws the task when udl cannot say whether its pull may merge", async () => {
		// (The e2e server has no GitHub account: the readiness is the one read that refuses.)
		const fake = fakeCtx((name) => {
			if (name === "orch_readiness") {
				throw new Error("No github account is connected");
			}
			return ANSWERS[name] as never;
		});
		const loaded = taskStore(fake.ctx, "29.2");
		await settle();

		const state = loaded.store.getState();
		expect(state.info?.task.key).toBe("29.2");
		expect(state.readiness).toBeNull();
		expect(state.error).toBeNull();
		loaded.stop();
	});

	it("draws the task when its estimate or its feature's tasks cannot be read", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_estimate" || name === "orch_list_tasks") {
				throw new Error("nope");
			}
			return ANSWERS[name] as never;
		});
		const loaded = taskStore(fake.ctx, "29.2");
		await settle();

		const state = loaded.store.getState();
		expect(state.info?.task.key).toBe("29.2");
		expect(state.info?.unblocks).toEqual([]);
		expect(state.estimate).toBeNull();
		expect(state.error).toBeNull();
		loaded.stop();
	});
});
