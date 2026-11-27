import { fireEvent, render, screen, within } from "@testing-library/react";
import type { RouteProps } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { ProgressPage } from "./progress";

/** What the page's two calls answer: a project with a history, its features and its models. */
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
			{ day: "2026-11-14", points: 236, merged: 100 },
			{ day: "2026-11-27", points: 236, merged: 48 },
		],
		// (The whole scope, and all of the points merged: 40 of them before the window's days.)
		points: 236,
		merged: 148,
		pace: 20,
		forecast: {
			likely: "2026-11-29T00:00:00Z",
			early: "2026-11-27T00:00:00Z",
			late: "2026-12-01T00:00:00Z",
			unsized: 2,
		},
		features: [
			{
				key: "29",
				title: "Screens and the orch UI plugin",
				points: 26,
				merged: 8,
				forecast: {
					likely: "2026-11-27T00:00:00Z",
					early: "2026-11-25T00:00:00Z",
					late: "2026-11-29T00:00:00Z",
				},
			},
			{ key: "30", title: "Moving in", points: 12, merged: 12, forecast: null },
		],
		models: [
			{
				model: "deepseek-v4.1-flash",
				runner: "opencode",
				tasks: 41,
				hours_per_point: { hours: 0.9, source: "model" },
				hours_per_size: { S: 0.8, M: 2.7, L: 4.4, XL: 7.0 },
			},
			{
				model: "claude-opus-5-5",
				runner: "claude-code",
				tasks: 9,
				hours_per_point: { hours: 0.5, source: "project" },
				hours_per_size: { S: 0.4, M: 1.5, L: 2.6, XL: 4.1 },
			},
		],
		recent: [
			{
				key: "27.6",
				title: "Readiness",
				size: "L",
				estimate: "PT4H24M",
				actual: "PT5H6M",
			},
			{
				key: "28.2",
				title: "Human actions",
				size: "M",
				estimate: "PT2H42M",
				actual: "PT2H12M",
			},
			{
				key: "18.10",
				title: "An example UI plugin",
				size: "M",
				estimate: "PT2H42M",
				actual: "PT3H24M",
			},
		],
		estimate_ratio: 1.12,
	},
};

/** The page, drawn with a fake udl behind it and the search it was opened with. */
function drawn(search: Record<string, string> = {}) {
	// (udl echoes the project it was asked for, as `orch_progress` does.)
	const fake = fakeCtx((name, request) =>
		name === "orch_progress"
			? ({ ...(ANSWERS.orch_progress as object), project: request.project } as never)
			: (ANSWERS[name] as never),
	);
	const props: RouteProps & { ctx: typeof fake.ctx } = {
		path: "progress",
		params: {},
		search,
		ctx: fake.ctx,
	};
	render(<ProgressPage {...props} />);
	return fake;
}

/** The page, read once. */
async function read(): Promise<void> {
	await settle();
	await settle();
}

describe("the progress page (29.11)", () => {
	it("draws the four numbers: merged, the forecast, the pace and the estimate ratio", async () => {
		drawn();
		await read();

		const stat = within(screen.getByText("Merged").closest("fieldset") as HTMLElement);
		expect(stat.getByText("148")).toBeTruthy();
		expect(screen.getByText("of 236 points · 63 %")).toBeTruthy();
		const ahead = within(screen.getByText("Forecast").closest("fieldset") as HTMLElement);
		expect(ahead.getByText("Sun 29 Nov")).toBeTruthy();
		expect(ahead.getByText("±2 days · 88 points left")).toBeTruthy();
		const pace = within(screen.getByText("Pace").closest("fieldset") as HTMLElement);
		expect(pace.getByText("20 pts/day")).toBeTruthy();
		const estimates = within(screen.getByText("Estimates").closest("fieldset") as HTMLElement);
		expect(estimates.getByText("1.12×")).toBeTruthy();
	});

	it("draws the burn-up, the features, the tasks merged lately and the learned hours", async () => {
		drawn();
		await read();

		expect(screen.getByLabelText("The burn-up's lines")).toBeTruthy();
		const features = within(screen.getByLabelText("By feature"));
		expect(features.getByText("Screens and the orch UI plugin")).toBeTruthy();
		expect(features.getByText("8/26")).toBeTruthy();
		expect(features.getByText("done")).toBeTruthy();
		const merged = within(screen.getByLabelText("Recently merged"));
		expect(merged.getByText("27.6")).toBeTruthy();
		expect(merged.getByText("4.4 h")).toBeTruthy();
		expect(merged.getByText("5.1 h")).toBeTruthy();
		expect(merged.getByText("1.16×")).toBeTruthy();
		const models = within(screen.getByLabelText("Hours per point"));
		expect(models.getByText("deepseek-v4.1-flash")).toBeTruthy();
		expect(models.getByText("opencode · 41 tasks")).toBeTruthy();
		expect(models.getByText("0.9")).toBeTruthy();
		expect(models.getByText("4.4")).toBeTruthy();
	});

	it("writes a duration it cannot read as no hours at all", async () => {
		const fake = fakeCtx((name) =>
			name === "orch_progress"
				? ({
						...(ANSWERS.orch_progress as object),
						recent: [{ key: "1.1", title: "Odd", size: "S", estimate: "soon", actual: "PT2H" }],
					} as never)
				: (ANSWERS[name] as never),
		);
		render(<ProgressPage ctx={fake.ctx} params={{}} path="progress" search={{}} />);
		await read();

		const merged = within(screen.getByLabelText("Recently merged"));
		expect(merged.getByText("0.0 h")).toBeTruthy();
		expect(merged.getByText("2.0 h")).toBeTruthy();
		// (No estimate is no ratio: a dash, not a green nothing.)
		expect(merged.getByText("–")).toBeTruthy();
	});

	it("counts the whole of the work, not only the days the window holds", async () => {
		const fake = fakeCtx((name) =>
			name === "orch_progress"
				? ({
						...(ANSWERS.orch_progress as object),
						// (40 points merged before the series' first day.)
						series: [
							{ day: "2026-11-13", points: 236, merged: 60 },
							{ day: "2026-11-27", points: 236, merged: 48 },
						],
					} as never)
				: (ANSWERS[name] as never),
		);
		render(<ProgressPage ctx={fake.ctx} params={{}} path="progress" search={{}} />);
		await read();

		const stat = within(screen.getByText("Merged").closest("fieldset") as HTMLElement);
		expect(stat.getByText("148")).toBeTruthy();
		expect(stat.getByText("of 236 points · 63 %")).toBeTruthy();
	});

	it("colours a ratio: amber over 1.25×, green under 0.9×, and plain in between", async () => {
		drawn();
		await read();

		const merged = within(screen.getByLabelText("Recently merged"));
		expect(merged.getByText("1.16×").className).not.toContain("warning");
		expect(merged.getByText("0.81×").className).toContain("success");
		expect(merged.getByText("1.26×").className).toContain("warning");
	});

	it("keeps the project and the day in the URL, and reads again when either changes", async () => {
		const fake = fakeCtx((name, request) =>
			name === "orch_progress"
				? ({ ...(ANSWERS.orch_progress as object), project: request.project } as never)
				: (ANSWERS[name] as never),
		);
		const view = (search: Record<string, string>) => (
			<ProgressPage ctx={fake.ctx} params={{}} path="progress" search={search} />
		);
		// (The shell navigates by drawing the page again with the new URL, which is what the
		// second change is made from.)
		const { rerender } = render(view({}));
		await read();

		fireEvent.change(screen.getByLabelText("Project"), { target: { value: "other" } });
		await read();
		expect(fake.navigations.at(-1)).toBe("/p/orch/progress?project=other");

		rerender(view({ project: "other" }));
		await read();
		fireEvent.change(screen.getByLabelText("Since"), { target: { value: "2026-11-13" } });
		await read();
		expect(fake.navigations.at(-1)).toBe("/p/orch/progress?project=other&since=2026-11-13");
		rerender(view({ project: "other", since: "2026-11-13" }));
		await read();
		expect(fake.calls.at(-1)?.request).toEqual({
			project: "other",
			since: "2026-11-13T00:00:00Z",
		});
	});

	it("reads the project the URL names, and the day it names", async () => {
		const fake = drawn({ project: "other", since: "2026-11-13" });
		await read();

		expect(fake.calls.at(-1)?.request).toEqual({
			project: "other",
			since: "2026-11-13T00:00:00Z",
		});
		expect((screen.getByLabelText("Project") as HTMLSelectElement).value).toBe("other");
		expect((screen.getByLabelText("Since") as HTMLSelectElement).value).toBe("2026-11-13");
	});

	it("says it is reading, and then says what a read failed with", async () => {
		const fake = fakeCtx(() => {
			throw new Error("udl is not answering");
		});
		render(<ProgressPage ctx={fake.ctx} params={{}} path="progress" search={{}} />);
		expect(screen.getByText("Reading the progress…")).toBeTruthy();
		await read();

		expect(screen.getByText("udl is not answering")).toBeTruthy();
	});

	it("says there is nothing to show when udl has no project", async () => {
		const fake = fakeCtx(() => ({ projects: [] }) as never);
		render(<ProgressPage ctx={fake.ctx} params={{}} path="progress" search={{}} />);
		await read();

		expect(screen.getByText("No project yet, so there is nothing to measure.")).toBeTruthy();
	});
});
