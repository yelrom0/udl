import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { FeaturePage } from "./feature";

/** What udl answers for 29: its spec, its tasks, one link and a note. */
const ANSWERS: Record<string, unknown> = {
	orch_get_feature: {
		feature: {
			key: "29",
			project: "udl",
			title: "Screens and the orch UI plugin",
			status: "active",
			spec: "## What it is\n\nThe screens.",
		},
		totals: { points: 13, points_merged: 5, points_ready: 3 },
		tasks: [
			{ key: "29.2", feature: "29", title: "The skeleton", state: "merged", size: "L" },
			{ key: "29.3", feature: "29", title: "The inbox", state: "in_progress", size: "M" },
		],
		links: [{ link_kind: "pull", url: "https://github.com/o/r/pull/80", title: "80", state: {} }],
		notes: [{ at: "2026-11-24T01:00:00Z", author: "claude", note_kind: "review", text: "ok" }],
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

/** The page, drawn, once its read has answered. */
async function read() {
	await settle();
	await screen.findByRole("heading", { level: 1, name: "Feature 29" });
}

describe("a feature's page (29.5)", () => {
	it("draws the feature: its key, its title, its state, its points and its spec", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<FeaturePage ctx={fake.ctx} params={{ key: "29" }} path="features/$key" search={{}} />);
		await read();

		expect(screen.getByText("Screens and the orch UI plugin")).toBeTruthy();
		expect(screen.getByText("active")).toBeTruthy();
		// (Its points, merged of all, and when they are likely done.)
		expect(screen.getByText("5 of 13 points")).toBeTruthy();
		expect(screen.getByText("likely Dec 1, 2026")).toBeTruthy();
		// (Its spec, as markdown.)
		expect(screen.getByRole("heading", { name: "What it is" })).toBeTruthy();
	});

	it("draws its tasks in order, each key a link to that task's page", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<FeaturePage ctx={fake.ctx} params={{ key: "29" }} path="features/$key" search={{}} />);
		await read();

		const tasks = screen.getByRole("list", { name: "Tasks" });
		const rows = within(tasks).getAllByRole("listitem");
		expect(rows).toHaveLength(2);
		expect(rows[0]?.textContent).toContain("The skeleton");
		expect(rows[0]?.textContent).toContain("merged");
		expect(rows[0]?.textContent).toContain("L");
		expect(
			within(rows[1] as HTMLElement)
				.getByRole("link", { name: "29.3" })
				.getAttribute("href"),
		).toBe("/ui/p/orch/tasks/29.3");
	});

	it("draws its links and its notes", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<FeaturePage ctx={fake.ctx} params={{ key: "29" }} path="features/$key" search={{}} />);
		await read();

		expect(screen.getByRole("list", { name: "Links" }).textContent).toContain("#80");
		expect(screen.getByRole("list", { name: "Notes" }).textContent).toContain("ok");
	});

	it("saves its spec, and reads the feature again", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<FeaturePage ctx={fake.ctx} params={{ key: "29" }} path="features/$key" search={{}} />);
		await read();
		const before = fake.calls.filter((one) => one.name === "orch_get_feature").length;

		fireEvent.click(
			within(screen.getByRole("region", { name: "Spec" })).getByRole("button", { name: "Edit" }),
		);
		fireEvent.change(screen.getByRole("textbox", { name: "Spec" }), {
			target: { value: "## New" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_set_feature")?.request).toEqual({
			key: "29",
			spec: "## New",
		});
		expect(fake.calls.filter((one) => one.name === "orch_get_feature").length).toBe(before + 1);
	});

	it("adds a task to the feature, and reads it again", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<FeaturePage ctx={fake.ctx} params={{ key: "29" }} path="features/$key" search={{}} />);
		await read();

		fireEvent.change(screen.getByRole("textbox", { name: "Title" }), {
			target: { value: "A new page" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add task" }));
		await settle();

		// (No key: udl takes the feature's next one.)
		expect(fake.calls.find((one) => one.name === "orch_set_task")?.request).toEqual({
			feature: "29",
			title: "A new page",
		});
	});

	it("splits a task into the pieces typed", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<FeaturePage ctx={fake.ctx} params={{ key: "29" }} path="features/$key" search={{}} />);
		await read();

		const row = within(screen.getByRole("list", { name: "Tasks" })).getAllByRole("listitem")[1];
		fireEvent.click(within(row as HTMLElement).getByRole("button", { name: "Split" }));
		fireEvent.change(screen.getByRole("textbox", { name: "Pieces" }), {
			target: { value: "The inbox page\nThe inbox store" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Split task" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_split_task")?.request).toEqual({
			key: "29.3",
			into: [{ title: "The inbox page" }, { title: "The inbox store" }],
		});
	});

	it("says what udl answered when the feature cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no such feature: '99'");
		});
		render(<FeaturePage ctx={fake.ctx} params={{ key: "99" }} path="features/$key" search={{}} />);

		expect(await screen.findByText(/no such feature/)).toBeTruthy();
	});
});
