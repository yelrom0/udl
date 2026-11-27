import { describe, expect, it } from "vitest";
import { BOARD, filterPath, filtersOf } from "./filters";

describe("the board's filters, kept in the URL (29.4)", () => {
	it("reads what the query says, and nothing when it says nothing", () => {
		expect(filtersOf({})).toEqual({ state: "", text: "" });
		expect(filtersOf({ state: "queued", text: "board" })).toEqual({
			state: "queued",
			text: "board",
		});
		// (Anything else in the query is the shell's, not the board's.)
		expect(filtersOf({ tab: "2" })).toEqual({ state: "", text: "" });
	});

	it("writes a filter back into the query, and drops one that is cleared", () => {
		expect(filterPath(BOARD, {}, { state: "queued" })).toBe(`${BOARD}?state=queued`);
		expect(filterPath(BOARD, { state: "queued" }, { text: "a b" })).toBe(
			`${BOARD}?state=queued&text=a+b`,
		);
		expect(filterPath(BOARD, { state: "queued", text: "board" }, { text: "" })).toBe(
			`${BOARD}?state=queued`,
		);
		// (Everything cleared is the page with no query at all.)
		expect(filterPath(BOARD, { state: "queued" }, { state: "" })).toBe(BOARD);
	});
});
