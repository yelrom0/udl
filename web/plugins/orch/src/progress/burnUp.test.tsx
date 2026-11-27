import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Forecast, SeriesPoint } from "../model";
import { BurnUp, niceStep, plot } from "./burnUp";

/** Three days of a fixed history: the scope grows once, and five points are merged. */
const SERIES: readonly SeriesPoint[] = [
	{ day: "2026-11-13", points: 10, merged: 0 },
	{ day: "2026-11-14", points: 10, merged: 3 },
	{ day: "2026-11-15", points: 20, merged: 2 },
];

/** What it is forecast to be done: likely on the 20th, between the 18th and the 22nd. */
const FORECAST: Forecast = {
	likely: "2026-11-20T00:00:00Z",
	early: "2026-11-18T00:00:00Z",
	late: "2026-11-22T00:00:00Z",
	unsized: 0,
};

const TODAY = new Date("2026-11-15T12:00:00Z");

describe("the burn-up's geometry (29.11)", () => {
	it("places each day, and the points merged up to it", () => {
		const drawn = plot(SERIES, FORECAST, TODAY);

		expect(drawn).not.toBeNull();
		expect(drawn?.merged).toHaveLength(3);
		// (The line is what is merged by the end of each day, not that day's own merge.)
		expect(drawn?.merged.map((one) => one.y)).toEqual([drawn?.y(0), drawn?.y(3), drawn?.y(5)]);
		// (The last day of the series is the last day of the merged line: nothing is drawn past
		// it as if it had merged more.)
		expect(drawn?.merged.at(-1)?.x).toBeCloseTo(drawn?.x(new Date("2026-11-15")) ?? 0, 5);
	});

	it("draws the scope as steps, at the points the tasks made by then come to", () => {
		const drawn = plot(SERIES, FORECAST, TODAY);

		// (A step per day: along at the day's scope, then up to the next day's.)
		expect(drawn?.scope).toHaveLength(5);
		expect(drawn?.scope.at(-1)?.y).toBeCloseTo(drawn?.y(20) ?? 0, 5);
	});

	it("runs the band from today to the forecast's early and late days, at the scope's end", () => {
		const drawn = plot(SERIES, FORECAST, TODAY);
		const band = drawn?.band ?? [];

		expect(band).toHaveLength(3);
		expect(band[1]?.x).toBeCloseTo(drawn?.x(new Date("2026-11-18")) ?? 0, 5);
		expect(band[2]?.x).toBeCloseTo(drawn?.x(new Date("2026-11-22")) ?? 0, 5);
		expect(band[1]?.y).toBeCloseTo(drawn?.y(20) ?? 0, 5);
		expect(band[2]?.y).toBeCloseTo(drawn?.y(20) ?? 0, 5);
	});

	it("carries the forecast from today's merged points to the scope's end on its likely day", () => {
		const drawn = plot(SERIES, FORECAST, TODAY);
		const forecast = drawn?.forecast ?? [];

		expect(forecast).toHaveLength(2);
		expect(forecast[0]?.y).toBeCloseTo(drawn?.y(5) ?? 0, 5);
		expect(forecast[1]?.x).toBeCloseTo(drawn?.x(new Date("2026-11-20")) ?? 0, 5);
		expect(forecast[1]?.y).toBeCloseTo(drawn?.y(20) ?? 0, 5);
	});

	it("keeps every point inside the chart, and ends its days at the forecast's last", () => {
		const drawn = plot(SERIES, FORECAST, TODAY);
		const points = [
			...(drawn?.merged ?? []),
			...(drawn?.scope ?? []),
			...(drawn?.forecast ?? []),
			...(drawn?.band ?? []),
		];

		expect(Math.max(...points.map((one) => one.x))).toBeLessThanOrEqual((drawn?.width ?? 0) - 16);
		expect(Math.min(...points.map((one) => one.x))).toBeGreaterThanOrEqual(40);
		expect(Math.min(...points.map((one) => one.y))).toBeGreaterThanOrEqual(0);
		expect(Math.max(...points.map((one) => one.y))).toBeLessThanOrEqual((drawn?.height ?? 0) - 30);
	});

	it("reads a nice top for the axis, and a tick for each step of it", () => {
		const drawn = plot(SERIES, FORECAST, TODAY);
		const values = (drawn?.yTicks ?? []).map((one) => one.value);

		expect(values[0]).toBe(0);
		expect(values.at(-1)).toBeGreaterThanOrEqual(20);
		expect(values).toEqual([...values].sort((one, two) => one - two));
	});

	it("rounds a scale's step up to the first of 1, 2, 2.5, 5 and 10 of its magnitude", () => {
		expect(niceStep(0)).toBe(1);
		expect(niceStep(1)).toBe(1);
		expect(niceStep(2)).toBe(2);
		expect(niceStep(2.5)).toBe(2.5);
		expect(niceStep(4)).toBe(5);
		expect(niceStep(8)).toBe(10);
		expect(niceStep(47)).toBe(50);
	});

	it("has nothing to draw with no days at all", () => {
		expect(plot([], FORECAST, TODAY)).toBeNull();
	});
});

describe("the burn-up (29.11)", () => {
	it("draws the three lines, the band, today, and the forecast's own date", () => {
		render(<BurnUp forecast={FORECAST} series={SERIES} today={TODAY} />);

		expect(screen.getByLabelText("merged")).toBeTruthy();
		expect(screen.getByLabelText("scope")).toBeTruthy();
		expect(screen.getByLabelText("forecast")).toBeTruthy();
		expect(screen.getByLabelText("likely range")).toBeTruthy();
		expect(screen.getByLabelText("today")).toBeTruthy();
		expect(screen.getByText("Fri 20 Nov")).toBeTruthy();
		expect(screen.getByText("scope 20")).toBeTruthy();
	});

	it("says the range without dates when the forecast has none, and draws nothing without a day", () => {
		const bare: Forecast = { likely: "", early: "", late: "", unsized: 0 };
		const { rerender } = render(<BurnUp forecast={bare} series={SERIES} today={TODAY} />);
		expect(screen.getByText("likely range")).toBeTruthy();

		rerender(<BurnUp forecast={bare} series={[]} today={TODAY} />);
		expect(screen.getByText("Nothing is sized yet, so there is no burn-up.")).toBeTruthy();
	});
});
