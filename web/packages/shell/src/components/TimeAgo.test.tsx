import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { relative, TimeAgo } from "./TimeAgo";

const NOW = new Date("2026-11-19T12:00:00Z");

beforeEach(() => {
	vi.useFakeTimers({ toFake: ["Date"] });
	vi.setSystemTime(NOW);
});

afterEach(() => {
	vi.useRealTimers();
});

describe("TimeAgo", () => {
	it("says how long ago it was", () => {
		render(<TimeAgo at="2026-11-19T11:58:00Z" />);

		const shown = screen.getByText(/minute/);
		expect(shown.tagName).toBe("TIME");
		expect(shown.getAttribute("datetime")).toBe("2026-11-19T11:58:00Z");
	});

	it("says 'just now' for this moment", () => {
		expect(relative(NOW.toISOString(), NOW)).toBe("just now");
	});

	it("counts up through the units", () => {
		const at = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

		expect(relative(at(30_000), NOW)).toMatch(/30 seconds ago/);
		expect(relative(at(2 * 3_600_000), NOW)).toMatch(/2 hours ago/);
		expect(relative(at(3 * 86_400_000), NOW)).toMatch(/3 days ago/);
		expect(relative(at(400 * 86_400_000), NOW)).toMatch(/last year/);
	});

	it("says a time that is ahead as such", () => {
		expect(relative("2026-11-19T13:00:00Z", NOW)).toMatch(/in 1 hour/);
	});

	it("shows a time it cannot read as it is", () => {
		render(<TimeAgo at="not a time" />);

		expect(screen.getByText("not a time")).toBeTruthy();
	});
});
