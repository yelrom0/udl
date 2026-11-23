import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { RouteProps } from "@udl/ui-sdk";
import { beforeEach, describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { moved } from "./agents";
import { BoardPage } from "./board";
import { carry } from "./drops";

/** What the board's six calls answer: one feature with three tasks, one agent, one suggestion. */
const ANSWERS: Record<string, unknown> = {
	orch_list_features: {
		project: "udl",
		features: [{ key: "29", title: "Screens", status: "active" }],
	},
	orch_list_tasks: {
		project: "udl",
		tasks: [
			{
				key: "29.2",
				feature: "29",
				title: "The skeleton",
				state: "in_progress",
				size: "L",
				assignee: "deepseek-a",
				needs: [],
			},
			{
				key: "29.3",
				feature: "29",
				title: "Inbox",
				state: "queued",
				size: "M",
				assignee: "deepseek-a",
				needs: [],
			},
			{
				key: "29.5",
				feature: "29",
				title: "Pages",
				state: "open",
				size: null,
				assignee: null,
				needs: ["29.3"],
			},
		],
	},
	orch_list_agents: {
		items: [
			{
				agent: { name: "deepseek-a", title: "deepseek-a", role: "implementer", model: "m-1" },
				status: "working",
				queue: [
					{
						key: "29.2",
						feature: "29",
						title: "The skeleton",
						state: "in_progress",
						position: 0,
					},
					{ key: "29.3", feature: "29", title: "Inbox", state: "queued", position: 1 },
				],
				reports: [],
			},
		],
	},
	orch_suggest: {
		project: "udl",
		suggestions: [
			{
				task: "29.5",
				title: "Pages",
				feature: "29",
				agent: "deepseek-a",
				score: 5,
				reasons: ["it is free", "same feature"],
				stack_on: "29.3",
			},
		],
	},
	orch_merge_list: {
		project: "udl",
		auto_merge: true,
		merger: "claude",
		ready: [
			{
				task: "29.2",
				feature: "29",
				title: "The skeleton",
				number: 80,
				url: "https://github.com/yelrom0/udl/pull/80",
				blockers: [],
			},
		],
		coming: [],
		held: [],
		done: [],
	},
	orch_progress: {
		project: "udl",
		features: [{ key: "29", title: "Screens", points: 26, merged: 8 }],
		models: [{ model: "m-1", hours_per_point: { hours: 1.5 } }],
	},
};

/** The board, drawn with a fake udl behind it. */
function drawn(search: Record<string, string> = {}) {
	const fake = fakeCtx((name) => ANSWERS[name] as never);
	const props: RouteProps & { ctx: typeof fake.ctx } = {
		path: "board",
		params: {},
		search,
		ctx: fake.ctx,
	};
	render(<BoardPage {...props} />);
	return fake;
}

/** What a drop asked udl to change (a re-read is not a change). */
function writes(fake: { readonly calls: { name: string }[] }): string[] {
	return fake.calls
		.map((one) => one.name)
		.filter(
			(name) => name === "orch_assign" || name === "orch_unassign" || name === "orch_reorder_queue",
		);
}

/** The board, read once. */
async function read(): Promise<void> {
	await waitFor(() => {
		expect(screen.getByRole("region", { name: /29 · Screens/ })).toBeTruthy();
	});
}

describe("the board (29.4)", () => {
	it("draws a feature, its bar and its tasks with their size and their pull", async () => {
		drawn();
		await read();

		const feature = screen.getByRole("region", { name: /29 · Screens/ });
		expect(within(feature).getByText("Screens")).toBeTruthy();
		// (Q-8: each feature's bar is its points merged out of its points.)
		expect(within(feature).getByText("8 of 26 points")).toBeTruthy();

		const task = within(feature).getByRole("article", { name: /29\.2/ });
		expect(within(task).getByRole("link", { name: "29.2" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/29.2",
		);
		expect(within(task).getByText("The skeleton")).toBeTruthy();
		// (Q-8: its size, and the PR's chip while its pull is open.)
		expect(within(task).getByText("L")).toBeTruthy();
		expect(within(task).getByRole("link", { name: /#80/ }).getAttribute("href")).toBe(
			"https://github.com/yelrom0/udl/pull/80",
		);
	});

	it("draws each agent's column: its dot, its queue in order, and its hours", async () => {
		drawn();
		await read();

		const column = screen.getByRole("region", { name: "deepseek-a" });
		expect(within(column).getByText("working")).toBeTruthy();
		// (The one it is on is on top; then the rest, in their order.)
		const queue = within(column).getByRole("list", { name: /queue/ });
		expect(
			within(queue)
				.getAllByRole("listitem")
				.map((one) => one.getAttribute("data-task")),
		).toEqual(["29.2", "29.3"]);
		// (Q-8 and 27.10: its points queued, and what they come to at its own rate: L + M = 8.)
		expect(within(column).getByText("8 points · 12 h")).toBeTruthy();
	});

	it("draws what is suggested, with its reasons and the agent it suits", async () => {
		drawn();
		await read();

		const suggested = screen.getByRole("region", { name: /suggested/i });
		expect(within(suggested).getByText("Pages")).toBeTruthy();
		expect(within(suggested).getByText("it is free · same feature")).toBeTruthy();
		expect(within(suggested).getByRole("button", { name: "Queue it" })).toBeTruthy();
	});

	it("keeps the state and the text it was given, and writes a new filter into the URL", async () => {
		const fake = drawn({ state: "queued", text: "" });
		await read();

		// (The state it was given is the state it shows.)
		expect(screen.queryByRole("article", { name: /29\.2/ })).toBeNull();
		expect(screen.getByRole("article", { name: /29\.3/ })).toBeTruthy();

		const box = screen.getByRole("searchbox", { name: /text/i });
		fireEvent.change(box, { target: { value: "inbox" } });
		// (Typing alone writes nothing: the URL takes one entry per search, not one per letter.)
		expect(fake.navigations).toEqual([]);
		fireEvent.keyDown(box, { key: "Enter" });

		expect(fake.navigations).toEqual(["/ui/p/orch/board?state=queued&text=inbox"]);
	});

	it("narrows the tasks by text, and says so when nothing is left", async () => {
		drawn({ state: "", text: "inbox" });
		await read();

		expect(screen.queryByRole("article", { name: /29\.2/ })).toBeNull();
		expect(screen.queryByRole("article", { name: /29\.5/ })).toBeNull();
	});

	it("says nothing is left, and shows the text the URL gave it", async () => {
		drawn({ state: "", text: "nothing like it" });
		await read();

		expect(screen.getByText("Nothing matches that.")).toBeTruthy();
		expect((screen.getByRole("searchbox", { name: /text/i }) as HTMLInputElement).value).toBe(
			"nothing like it",
		);
	});
});

describe("the board's drops (29.4)", () => {
	// (What a drag carries lives for one drag, and a test's drag is not the next one's.)
	beforeEach(() => {
		carry(null);
	});

	it("assigns what is dragged onto an agent's column, with its project", async () => {
		const fake = drawn();
		await read();

		fireEvent.dragStart(screen.getByRole("article", { name: "29.5 Pages" }));
		fireEvent.drop(screen.getByRole("region", { name: "deepseek-a" }));
		await settle();

		expect(fake.calls.map((one) => one.name)).toContain("orch_assign");
		expect(fake.calls.find((one) => one.name === "orch_assign")?.request).toEqual({
			key: "29.5",
			agent: "deepseek-a",
			project: "udl",
		});
	});

	it("reorders a queue's own entry, and asks for nothing else", async () => {
		const fake = drawn();
		await read();

		const queue = within(screen.getByRole("region", { name: "deepseek-a" })).getByRole("list", {
			name: /queue/,
		});
		const [first, second] = within(queue).getAllByRole("listitem");
		fireEvent.dragStart(first as HTMLElement);
		fireEvent.drop(second as HTMLElement);
		await settle();

		// (One write: the reorder, never the column's assign after it.)
		expect(fake.calls.map((one) => one.name).filter((one) => one.startsWith("orch_"))).toContain(
			"orch_reorder_queue",
		);
		expect(fake.calls.some((one) => one.name === "orch_assign")).toBe(false);
		expect(fake.calls.find((one) => one.name === "orch_reorder_queue")?.request).toEqual({
			agent: "deepseek-a",
			keys: ["29.3", "29.2"],
			project: "udl",
		});
	});

	it("assigns a task from another queue onto an entry, at that entry's position", async () => {
		const fake = drawn();
		await read();

		// (29.5 is nobody's yet: dropping it on the queue's second entry is an assign, not a
		// reorder of keys that queue does not have.)
		const queue = within(screen.getByRole("region", { name: "deepseek-a" })).getByRole("list", {
			name: /queue/,
		});
		const entries = within(queue).getAllByRole("listitem");
		carry({ kind: "task", key: "29.5" });
		fireEvent.drop(entries[1] as HTMLElement);
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_assign")?.request).toEqual({
			key: "29.5",
			agent: "deepseek-a",
			project: "udl",
			position: 1,
		});
		expect(fake.calls.some((one) => one.name === "orch_reorder_queue")).toBe(false);
	});

	it("unassigns a task dropped back on the features pane, and nothing else", async () => {
		const fake = drawn();
		await read();

		fireEvent.dragStart(screen.getByRole("article", { name: /29\.2/ }));
		fireEvent.drop(screen.getByRole("region", { name: "Features" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_unassign")?.request).toEqual({
			key: "29.2",
			project: "udl",
		});
	});

	it("asks for nothing when a task nobody holds is dropped on the features pane", async () => {
		const fake = drawn();
		await read();

		fireEvent.dragStart(screen.getByRole("article", { name: "29.5 Pages" }));
		fireEvent.drop(screen.getByRole("region", { name: "Features" }));
		await settle();

		expect(fake.calls.some((one) => one.name === "orch_unassign")).toBe(false);
	});

	it("forgets what a cancelled drag carried", async () => {
		const fake = drawn();
		await read();

		fireEvent.dragStart(screen.getByRole("article", { name: "29.5 Pages" }));
		fireEvent.dragEnd(screen.getByRole("article", { name: "29.5 Pages" }));
		fireEvent.drop(screen.getByRole("region", { name: "deepseek-a" }));
		await settle();

		expect(fake.calls.some((one) => one.name === "orch_assign")).toBe(false);
	});
});

describe("the board's other paths (29.4)", () => {
	beforeEach(() => {
		carry(null);
	});

	it("queues a suggestion from its own button, asking the stack question first", async () => {
		const fake = drawn();
		fake.yes = true;
		await read();

		fireEvent.click(
			within(screen.getByRole("region", { name: "Suggested" })).getByRole("button", {
				name: "Queue it",
			}),
		);
		await settle();

		// (The button is the same drop a drag makes, so its need is asked about too.)
		expect(fake.asked).toEqual(["Stack on #29.3?"]);
		expect(fake.calls.find((one) => one.name === "orch_assign")?.request).toEqual({
			key: "29.5",
			agent: "deepseek-a",
			project: "udl",
		});
	});

	it("assigns from the card's menu, which is the keyboard's path", async () => {
		const fake = drawn();
		await read();

		const card = screen.getByRole("article", { name: /29\.2/ });
		fireEvent.click(within(card).getByRole("button", { name: "Assign to…" }));
		fireEvent.click(within(card).getByRole("menuitem", { name: "deepseek-a" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_assign")?.request).toEqual({
			key: "29.2",
			agent: "deepseek-a",
			project: "udl",
		});
	});

	it("says why a drop was refused, and reads the board again", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_assign") {
				throw new Error("29.5 is a person's task");
			}
			return ANSWERS[name] as never;
		});
		render(<BoardPage ctx={fake.ctx} params={{}} path="board" search={{}} />);
		await read();

		fireEvent.dragStart(screen.getByRole("article", { name: "29.5 Pages" }));
		fireEvent.drop(screen.getByRole("region", { name: "deepseek-a" }));
		await settle();

		expect(fake.toasts).toEqual([{ message: "29.5 is a person's task", kind: "error" }]);
		// (Snapped back: the board is read again, so what it shows is udl's.)
		const reads = fake.calls.filter((one) => one.name === "orch_list_features");
		expect(reads.length).toBeGreaterThan(1);
	});

	it("asks before stacking a suggestion on its need, and does nothing on no", async () => {
		const fake = drawn();
		fake.yes = false;
		await read();

		fireEvent.dragStart(screen.getByRole("article", { name: "Suggested: 29.5 Pages" }));
		fireEvent.drop(screen.getByRole("region", { name: "deepseek-a" }));
		await settle();

		expect(fake.asked).toEqual(["Stack on #29.3?"]);
		expect(fake.calls.some((one) => one.name === "orch_assign")).toBe(false);
	});

	it("writes the state filter into the URL too", async () => {
		const fake = drawn({ state: "", text: "" });
		await read();

		fireEvent.change(screen.getByRole("combobox", { name: "State" }), {
			target: { value: "merged" },
		});

		expect(fake.navigations).toEqual(["/ui/p/orch/board?state=merged"]);
	});

	it("says when there is nothing to suggest, or no agents", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_suggest") {
				return { project: "udl", suggestions: [] };
			}
			if (name === "orch_list_agents") {
				return { items: [] };
			}
			return ANSWERS[name] as never;
		});
		render(<BoardPage ctx={fake.ctx} params={{}} path="board" search={{}} />);
		await read();

		expect(screen.getByText("No agents yet.")).toBeTruthy();
		expect(screen.getByText("Nothing is ready to start.")).toBeTruthy();
	});

	it("says what udl answered when the board cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("udl answered 503: it is restarting");
		});
		render(<BoardPage ctx={fake.ctx} params={{}} path="board" search={{}} />);

		await waitFor(() => {
			expect(screen.getByText(/it is restarting/)).toBeTruthy();
		});
	});
});

describe("the board's quiet edges (29.4)", () => {
	beforeEach(() => {
		carry(null);
	});

	it("draws an agent with no queue, no rate and a status it does not know", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_agents") {
				return {
					items: [
						{
							agent: { name: "quiet", title: "quiet", role: "implementer", model: "m-9" },
							status: "napping",
							queue: [],
							reports: [],
						},
					],
				};
			}
			return ANSWERS[name] as never;
		});
		render(<BoardPage ctx={fake.ctx} params={{}} path="board" search={{}} />);
		await read();

		const column = screen.getByRole("region", { name: "quiet" });
		expect(within(column).getByText("Nothing on its queue.")).toBeTruthy();
		// (No rate for its model: its points, and no hours claimed.)
		expect(within(column).getByText("0 points")).toBeTruthy();
		expect(within(column).getByRole("img", { name: "napping" })).toBeTruthy();
	});

	it("asks for nothing when a queue entry is dropped with no drag behind it", async () => {
		const fake = drawn();
		await read();

		const queue = within(screen.getByRole("region", { name: "deepseek-a" })).getByRole("list", {
			name: /queue/,
		});
		const [first] = within(queue).getAllByRole("listitem");
		fireEvent.drop(first as HTMLElement);
		await settle();

		expect(writes(fake)).toEqual([]);
	});

	it("asks for nothing when an entry is dropped on itself", async () => {
		const fake = drawn();
		await read();

		const queue = within(screen.getByRole("region", { name: "deepseek-a" })).getByRole("list", {
			name: /queue/,
		});
		const [first] = within(queue).getAllByRole("listitem");
		fireEvent.dragStart(first as HTMLElement);
		fireEvent.drop(first as HTMLElement);
		await settle();

		expect(writes(fake)).toEqual([]);
	});
});

describe("a queue's order (29.4)", () => {
	it("puts a task where it was dropped: before the entry going up, after it going down", () => {
		// (Down: the top card dropped on the last goes after it.)
		expect(moved(["a", "b", "c"], "a", "c")).toEqual(["b", "c", "a"]);
		// (Up: after 29.3, dropped on 29.2 → before it.)
		expect(moved(["a", "b", "c"], "c", "a")).toEqual(["c", "a", "b"]);
		// (On itself: where it was.)
		expect(moved(["a", "b"], "a", "a")).toEqual(["a", "b"]);
		// (An entry that is not there at all: the end, not a hole in the middle.)
		expect(moved(["a", "b"], "a", "gone")).toEqual(["b", "a"]);
	});
});
