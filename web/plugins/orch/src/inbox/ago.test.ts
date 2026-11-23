import { describe, expect, it } from "vitest";
import { ago } from "./ago";

describe("how long ago", () => {
	const now = Date.parse("2026-11-23T13:30:00Z");

	it.each([
		["2026-11-23T13:30:00Z", "0 s ago"],
		["2026-11-23T13:29:30Z", "30 s ago"],
		["2026-11-23T13:28:00Z", "2 min ago"],
		["2026-11-23T12:30:00Z", "1 h ago"],
		["2026-11-22T13:30:00Z", "1 d ago"],
		["2026-11-16T13:30:00Z", "7 d ago"],
	])("says %s as %s", (iso, said) => {
		expect(ago(iso, now)).toBe(said);
	});

	it("says nothing for a time it cannot read", () => {
		expect(ago("not a time", now)).toBe("");
	});

	it("does not say a time in the future is in the past", () => {
		expect(ago("2026-11-23T13:31:00Z", now)).toBe("0 s ago");
	});
});
