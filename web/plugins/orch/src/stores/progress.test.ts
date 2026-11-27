import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { progressStore } from "./progress";

/** What the page's two calls answer, as the plugin reads them. */
const ANSWERS: Record<string, unknown> = {
	orch_list_projects: {
		projects: [
			{ name: "udl", title: "udl" },
			{ name: "other", title: "Another" },
		],
	},
	orch_progress: {
		project: "udl",
		series: [
			{ day: "2026-11-13", points: 236, merged: 0 },
			{ day: "2026-11-14", points: 236, merged: 3 },
		],
		pace: 20,
		forecast: {
			likely: "2026-11-27T00:00:00Z",
			early: "2026-11-25T00:00:00Z",
			late: "2026-11-29T00:00:00Z",
		},
		features: [{ key: "29", title: "Screens", points: 26, merged: 8, forecast: null }],
		models: [
			{
				model: "deepseek-v4.1-flash",
				runner: "opencode",
				tasks: 41,
				hours_per_point: { hours: 0.9, source: "model" },
				hours_per_size: { S: 0.8 },
			},
		],
		recent: [{ key: "27.6", title: "Readiness", size: "L", estimate: "PT4H", actual: "PT5H" }],
		estimate_ratio: 1.16,
	},
};

describe("the progress store (29.11)", () => {
	it("reads the projects, then the progress of the one asked for", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);

		const { store, stop } = progressStore(fake.ctx, "udl", "");
		await settle();

		expect(fake.calls[0]?.name).toBe("orch_list_projects");
		expect(fake.calls[1]?.name).toBe("orch_progress");
		expect(fake.calls[1]?.request).toEqual({ project: "udl" });
		const state = store.getState();
		expect(state.projects).toEqual(["udl", "other"]);
		expect(state.project).toBe("udl");
		expect(state.report?.series).toHaveLength(2);
		expect(state.report?.forecast.likely).toBe("2026-11-27T00:00:00Z");
		expect(state.loaded).toBe(true);
		expect(state.error).toBeNull();
		stop();
	});

	it("takes the first project when the page names none, and asks for the day it was given", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);

		const { store, stop } = progressStore(fake.ctx, "", "2026-11-13");
		await settle();

		// (The page's day is a date; udl's `since` is an aware datetime, and a bare date is
		// refused as naive.)
		expect(fake.calls[1]?.request).toEqual({
			project: "udl",
			since: "2026-11-13T00:00:00Z",
		});
		expect(store.getState().project).toBe("udl");
		stop();
	});

	it("reads it again when a task merges, and once for a burst of events", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const { stop } = progressStore(fake.ctx, "udl", "");
		await settle();

		fake.emit("orch.task.merged", { task: "29.2" });
		await settle();
		expect(fake.calls).toHaveLength(4);

		for (let line = 0; line < 20; line += 1) {
			fake.emit("orch.session.ended", { agent: "deepseek-a" });
		}
		await settle();
		expect(fake.calls).toHaveLength(6);
		stop();
	});

	it("says what a read failed with, and keeps what it drew", async () => {
		let fail = false;
		const fake = fakeCtx((name) => {
			if (fail) {
				throw new Error("udl is not answering");
			}
			return ANSWERS[name] as never;
		});
		const { store, stop } = progressStore(fake.ctx, "udl", "");
		await settle();

		fail = true;
		await store.getState().project;
		fake.emit("orch.task.merged", { task: "29.2" });
		await settle();

		expect(store.getState().error).toBe("udl is not answering");
		expect(store.getState().report?.project).toBe("udl");
		stop();
	});

	it("reads no report at all when udl has no project", async () => {
		const fake = fakeCtx((name) =>
			name === "orch_list_projects" ? ({ projects: [] } as never) : (ANSWERS[name] as never),
		);

		const { store, stop } = progressStore(fake.ctx, "", "");
		await settle();

		expect(fake.calls.map((one) => one.name)).toEqual(["orch_list_projects"]);
		expect(store.getState().report).toBeNull();
		expect(store.getState().loaded).toBe(true);
		stop();
	});
});
