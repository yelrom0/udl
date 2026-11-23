import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { featureStore } from "./feature";

/** What the calls a feature's page reads answer. */
const ANSWERS: Record<string, unknown> = {
	orch_get_feature: {
		feature: {
			key: "29",
			project: "udl",
			title: "Screens",
			status: "active",
			spec: "## What it is",
		},
		totals: { points: 13, points_merged: 5, points_ready: 3 },
		tasks: [
			{ key: "29.2", feature: "29", title: "The skeleton", state: "merged", size: "L" },
			{ key: "29.3", feature: "29", title: "The inbox", state: "in_progress", size: "M" },
		],
		links: [],
		notes: [{ at: "2026-11-24T01:00:00Z", author: "claude", note_kind: "note", text: "hi" }],
	},
	orch_merge_list: {
		project: "udl",
		ready: [
			{
				task: "29.3",
				feature: "29",
				title: "The inbox",
				number: 99,
				url: "https://github.com/yelrom0/udl/pull/99",
			},
		],
		coming: [
			{
				task: "29.2",
				feature: "29",
				title: "The skeleton",
				number: 98,
				url: "https://github.com/yelrom0/udl/pull/98",
			},
		],
		held: [],
		done: [{ task: "28.7", feature: "28", title: "Merged", number: 78, url: "u" }],
	},
	orch_progress: {
		project: "udl",
		features: [
			{ key: "29", title: "Screens", points: 13, merged: 5, forecast: { likely: "2026-12-01" } },
		],
		models: [],
		pace: 1.5,
	},
};

describe("a feature's store (29.5)", () => {
	it("reads the feature, and its own row of the project's progress", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = featureStore(fake.ctx, "29");

		await settle();

		const state = loaded.store.getState();
		expect(state.info?.feature.key).toBe("29");
		expect(state.info?.spec).toBe("## What it is");
		expect(state.info?.tasks.map((task) => task.key)).toEqual(["29.2", "29.3"]);
		// (Its row: what its tasks come to, and when they are likely done.)
		expect(state.points).toEqual({ points: 13, merged: 5, forecast: "2026-12-01" });
		expect(state.pace).toBe(1.5);
		// (The pulls open on its tasks: ready or coming, never a merged one.)
		expect(state.pulls["29.3"]).toEqual({
			number: 99,
			url: "https://github.com/yelrom0/udl/pull/99",
		});
		expect(state.pulls["29.2"]?.number).toBe(98);
		expect(state.pulls["28.7"]).toBeUndefined();
		expect(state.error).toBeNull();
		loaded.stop();
	});

	it("asks for the feature by its key, and the progress for its project", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = featureStore(fake.ctx, "29");
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_get_feature")?.request).toEqual({
			key: "29",
		});
		// (Its project's progress: the feature's own answer says which project that is.)
		expect(fake.calls.find((one) => one.name === "orch_progress")?.request).toEqual({
			project: "udl",
		});
		loaded.stop();
	});

	it("says what udl answered when the feature cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no such feature: '99'");
		});
		const loaded = featureStore(fake.ctx, "99");
		await settle();

		expect(loaded.store.getState().error).toContain("no such feature");
		loaded.stop();
	});

	it("reads again when a task of it moves, or a note is added", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = featureStore(fake.ctx, "29");
		await settle();
		const before = fake.calls.filter((one) => one.name === "orch_get_feature").length;

		fake.emit("orch.task.changed");
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_get_feature").length).toBe(before + 1);
		loaded.stop();
	});

	it("stops listening when the page goes away", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const loaded = featureStore(fake.ctx, "29");
		await settle();
		loaded.stop();
		const before = fake.calls.length;

		fake.emit("orch.task.changed");
		await settle();

		expect(fake.calls.length).toBe(before);
	});
});

describe("a feature's store's reads beside the feature (29.5)", () => {
	it("draws the feature when the project's progress cannot be read", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_progress") {
				throw new Error("nope");
			}
			return ANSWERS[name] as never;
		});
		const loaded = featureStore(fake.ctx, "29");
		await settle();

		const state = loaded.store.getState();
		expect(state.info?.feature.key).toBe("29");
		expect(state.points.points).toBe(0);
		expect(state.error).toBeNull();
		loaded.stop();
	});
});
