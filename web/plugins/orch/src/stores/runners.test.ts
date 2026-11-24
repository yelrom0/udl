import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { runnersStore } from "./runners";

/** What a host answers for the harnesses it has. */
const ANSWER = {
	items: [
		{
			host: "local",
			runner: "opencode",
			health: { installed: true, signed_in: true, version: "1.2.3" },
			models: [{ id: "deepseek-v4.1", name: "DeepSeek V4.1" }],
		},
	],
	problems: [],
};

describe("the harnesses' store (29.6, 25.11)", () => {
	it("reads what every host has, and that it answered", async () => {
		const fake = fakeCtx(() => ANSWER as never);
		const loaded = runnersStore(fake.ctx);
		await settle();

		const state = loaded.store.getState();
		expect(state.items.map((one) => one.runner)).toEqual(["opencode"]);
		expect(state.items[0]?.models.map((one) => one.id)).toEqual(["deepseek-v4.1"]);
		expect(state.answered).toBe(true);
		expect(state.loaded).toBe(true);
		expect(state.error).toBeNull();
	});

	it("says which hosts could not be asked, and that some did", async () => {
		const fake = fakeCtx(
			() => ({ ...ANSWER, problems: ["jeff-wiggle: no route to host"] }) as never,
		);
		const loaded = runnersStore(fake.ctx);
		await settle();

		expect(loaded.store.getState().problems).toEqual(["jeff-wiggle: no route to host"]);
		expect(loaded.store.getState().answered).toBe(true);
	});

	it("takes a name as text when no host answered at all", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no host is up");
		});
		const loaded = runnersStore(fake.ctx);
		await settle();

		const state = loaded.store.getState();
		expect(state.answered).toBe(false);
		expect(state.loaded).toBe(true);
		expect(state.error).toBe("no host is up");
	});

	it("reads again when it is asked to, and says nothing after it stops", async () => {
		let answer: unknown = ANSWER;
		const fake = fakeCtx(() => answer as never);
		const loaded = runnersStore(fake.ctx);
		await settle();

		answer = { items: [], problems: [] };
		await loaded.refresh();
		expect(loaded.store.getState().items).toEqual([]);

		loaded.stop();
		await loaded.refresh();
		expect(loaded.store.getState().items).toEqual([]);
	});
});
