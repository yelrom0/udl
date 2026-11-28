import { describe, expect, it } from "vitest";
import type { AgentUsage } from "../model";
import { usageOf } from "../model";
import { momentOf, usageLineOf, usageToneOf, usageWordsOf } from "./usage";

/** What one agent has left, as `orch_usage` answers it. */
function usageOf_(row: Record<string, unknown>): AgentUsage {
	return usageOf({
		agent: "deepseek-a",
		runner: "opencode",
		requests_today: 12,
		tokens_in: 24000,
		tokens_out: 3100,
		limited_until: null,
		state: "ok",
		...row,
	});
}

describe("an agent's usage, in a line (29.6-usage)", () => {
	it("says a paid key's money left, of what it may spend", () => {
		const paid = usageOf_({
			model: "openrouter-ds-a/deepseek-v4.1-flash",
			paid: { spent: "1.64", limit: "8", left: "6.36", spent_today: "0.21" },
			free: null,
		});

		expect(usageLineOf(paid)).toBe("$6.36 of $8 left");
		expect(usageWordsOf(paid)).toEqual([
			"Spent $1.64 of $8",
			"$0.21 today",
			"12 requests, 24,000 in, 3,100 out",
		]);
	});

	it("says a paid key's money left when it has no limit", () => {
		const paid = usageOf_({
			model: "openrouter-ds-a/deepseek-v4.1-flash",
			paid: { spent: "1.64", limit: null, left: "3.36" },
			free: null,
		});

		expect(usageLineOf(paid)).toBe("$3.36 left");
	});

	it("says a free model's requests left, and when they reset", () => {
		const free = usageOf_({
			model: "openrouter/ernie/free",
			paid: null,
			free: {
				requests_today: 412,
				allowance: 1000,
				left: 588,
				resets_at: "2026-11-29T00:00:00Z",
			},
		});

		// (The reset is a moment, so it is read in the reader's own time zone.)
		const reset = new Date("2026-11-29T00:00:00Z");
		const when = `${reset.getFullYear()}-${String(reset.getMonth() + 1).padStart(2, "0")}-${String(
			reset.getDate(),
		).padStart(2, "0")} ${String(reset.getHours()).padStart(2, "0")}:${String(
			reset.getMinutes(),
		).padStart(2, "0")}`;
		expect(usageLineOf(free)).toBe(`588 of 1,000 requests left, resets ${when}`);
	});

	it("says nothing of an agent there is nothing to measure for", () => {
		const nothing = usageOf_({ model: "deepseek-v4.1", paid: null, free: null });

		expect(usageLineOf(nothing)).toBe("");
		expect(usageToneOf(nothing)).toBe("text-base-content/60");
	});

	it("colours what is running low, and what is out", () => {
		const low = usageOf_({ state: "low", paid: { left: "0.4", limit: "8" }, free: null });
		const out = usageOf_({ state: "out", paid: { left: "0", limit: "8" }, free: null });
		const ok = usageOf_({ state: "ok", paid: { left: "6", limit: "8" }, free: null });

		expect(usageToneOf(low)).toBe("text-warning");
		expect(usageToneOf(out)).toBe("text-error");
		expect(usageToneOf(ok)).toBe("text-base-content/60");
	});

	it("says a harness's own limit, when its session is out of quota", () => {
		const limited = usageOf_({
			model: "deepseek-v4.1",
			paid: null,
			free: null,
			limited_until: "2026-11-28T12:00:00Z",
		});

		// (Read in the reader's own time zone, as the reset is.)
		expect(usageLineOf(limited)).toBe(`limited until ${momentOf("2026-11-28T12:00:00Z")}`);
		expect(usageToneOf(limited)).toBe("text-warning");
	});
});
