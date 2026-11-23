import { describe, expect, it } from "vitest";
import { filterPath, filtersOf } from "./filters";

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
		expect(filterPath("/ui/p/orch/board", {}, { state: "queued" })).toBe(
			"/ui/p/orch/board?state=queued",
		);
		expect(filterPath("/ui/p/orch/board", { state: "queued" }, { text: "a b" })).toBe(
			"/ui/p/orch/board?state=queued&text=a+b",
		);
		expect(filterPath("/ui/p/orch/board", { state: "queued", text: "board" }, { text: "" })).toBe(
			"/ui/p/orch/board?state=queued",
		);
		// (Everything cleared is the page with no query at all.)
		expect(filterPath("/ui/p/orch/board", { state: "queued" }, { state: "" })).toBe(
			"/ui/p/orch/board",
		);
	});
});
