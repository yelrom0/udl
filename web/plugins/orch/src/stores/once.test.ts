import { describe, expect, it } from "vitest";

import { settle } from "../testing/fake";
import { coalesced } from "./once";

/** A read that is finished by hand, and how many times it was started. */
function slow(): { started: number; run: () => Promise<void>; finish: () => void } {
	const hands: (() => void)[] = [];
	const one = {
		started: 0,
		run: async (): Promise<void> => {
			one.started += 1;
			await new Promise<void>((done) => {
				hands.push(done);
			});
		},
		finish: () => {
			for (const done of hands.splice(0)) {
				done();
			}
		},
	};
	return one;
}

describe("one read at a time", () => {
	it("reads once for asks made together, before the read starts", async () => {
		const one = slow();
		const { ask, stop } = coalesced(one.run);

		ask();
		ask();
		ask();
		await settle();

		// (Three asks in the same breath are one read.)
		expect(one.started).toBe(1);
		one.finish();
		await settle();
		expect(one.started).toBe(1);
		stop();
	});

	it("asks once more for asks made while a read is in flight, however many", async () => {
		const one = slow();
		const { ask, stop } = coalesced(one.run);

		ask();
		await settle();
		expect(one.started).toBe(1);

		ask();
		ask();
		one.finish();
		await settle();
		// (Everything asked while it read is one read after it, not three.)
		expect(one.started).toBe(2);

		one.finish();
		await settle();
		expect(one.started).toBe(2);
		stop();
	});

	it("answers every ask it was given, including one it never read", async () => {
		const one = slow();
		const { ask, stop } = coalesced(one.run);

		const first = ask();
		await settle();
		const second = ask();
		stop();
		one.finish();

		await expect(Promise.all([first, second])).resolves.toBeDefined();
		expect(one.started).toBe(1);
	});

	it("starts the next read when one fails, and says what failed", async () => {
		let tries = 0;
		const said: string[] = [];
		const { ask, stop } = coalesced(
			async () => {
				tries += 1;
				if (tries === 1) {
					throw new Error("udl is not answering");
				}
			},
			(error) => {
				said.push(error instanceof Error ? error.message : String(error));
			},
		);

		ask();
		await settle();
		ask();
		await settle();

		expect(tries).toBe(2);
		expect(said).toEqual(["udl is not answering"]);
		stop();
	});

	it("asks nothing after it is stopped", async () => {
		const one = slow();
		const { ask, stop } = coalesced(one.run);

		stop();
		ask();
		await settle();

		expect(one.started).toBe(0);
	});

	it("finishes the read it was in, and starts nothing after it", async () => {
		const one = slow();
		const { ask, stop } = coalesced(one.run);

		ask();
		await settle();
		ask();
		stop();
		one.finish();
		await settle();

		expect(one.started).toBe(1);
	});
});
