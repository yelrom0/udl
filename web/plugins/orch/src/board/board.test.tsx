import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { RouteProps } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { BoardPage } from "./board";

/** What the board's six calls answer: one feature with two tasks, one agent, one suggestion. */
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
		models: [{ model: "m-1", runner: "claude-code", tasks: 9, hours_per_point: { hours: 1.5 } }],
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

describe("the board (29.4)", () => {
	it("draws a feature, its bar and its tasks with their size and their pull", async () => {
		drawn();
		await waitFor(() => {
			expect(screen.getByRole("heading", { level: 2, name: /29/ })).toBeTruthy();
		});

		const feature = screen.getByRole("region", { name: /29/ });
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
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /deepseek-a/ })).toBeTruthy();
		});

		const column = screen.getByRole("region", { name: /deepseek-a/ });
		expect(within(column).getByText("working")).toBeTruthy();
		// (The one it is on is on top; then the rest, in their order.)
		const queue = within(column).getByRole("list", { name: /queue/i });
		expect(
			within(queue)
				.getAllByRole("listitem")
				.map((one) => one.getAttribute("data-task")),
		).toEqual(["29.2", "29.3"]);
		// (Q-8 and 27.10: its points queued, and what they come to at its own rate.)
		expect(within(column).getByText("8 points · 12 h")).toBeTruthy();
	});

	it("draws what is suggested, with its reasons and the agent it suits", async () => {
		drawn();
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /suggested/i })).toBeTruthy();
		});

		const suggested = screen.getByRole("region", { name: /suggested/i });
		expect(within(suggested).getByText("Pages")).toBeTruthy();
		expect(within(suggested).getByText("it is free · same feature")).toBeTruthy();
		expect(within(suggested).getByRole("button", { name: "Queue it" })).toBeTruthy();
	});

	it("keeps the state and the text it was given, and writes a new filter into the URL", async () => {
		const fake = drawn({ state: "queued", text: "" });
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /29/ })).toBeTruthy();
		});

		// (The state it was given is the state it shows.)
		const features = within(screen.getByRole("region", { name: "Features" }));
		expect(features.queryByRole("article", { name: /29\.2/ })).toBeNull();
		expect(features.getByRole("article", { name: /29\.3/ })).toBeTruthy();

		fireEvent.change(screen.getByRole("searchbox", { name: /text/i }), {
			target: { value: "inbox" },
		});

		expect(fake.navigations).toEqual(["/ui/p/orch/board?state=queued&text=inbox"]);
	});

	it("narrows the tasks by text, and says so when nothing is left", async () => {
		drawn({ state: "", text: "inbox" });
		await waitFor(() => {
			expect(screen.getByRole("region", { name: "Features" })).toBeTruthy();
		});

		// (The suggested pane still names 29.5: it is a suggestion, not a task card.)
		const features = within(screen.getByRole("region", { name: "Features" }));
		expect(features.getByRole("article", { name: /29\.3/ })).toBeTruthy();
		expect(features.queryByRole("article", { name: /29\.2/ })).toBeNull();
		expect(features.queryByRole("article", { name: /29\.5/ })).toBeNull();

		const other = fakeCtx((name) => ANSWERS[name] as never);
		// (A second board, drawn beside the first: the empty states are the second's.)
		render(
			<BoardPage
				path="board"
				params={{}}
				search={{ state: "", text: "nothing like it" }}
				ctx={other.ctx}
			/>,
		);
		await settle();
		expect(screen.getByText("Nothing matches that.")).toBeTruthy();
	});

	it("says what is empty, rather than drawing nothing", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_features") {
				return {
					project: "udl",
					features: [{ key: "30", title: "Nothing yet", status: "planned" }],
				};
			}
			if (name === "orch_list_agents") {
				return {
					items: [
						{
							agent: { name: "a", title: "a", role: "implementer", model: "unlearned" },
							status: "",
							queue: [],
							reports: [],
						},
					],
				};
			}
			if (name === "orch_suggest") {
				return { project: "udl", suggestions: [] };
			}
			return { project: "udl", tasks: [], features: [], models: [] };
		});
		render(<BoardPage path="board" params={{}} search={{}} ctx={fake.ctx} />);
		await waitFor(() => {
			expect(screen.getByText("No tasks match.")).toBeTruthy();
		});

		expect(screen.getByText("Nothing on its queue.")).toBeTruthy();
		// (A model nothing has been learned of yet is its points, and no hours.)
		expect(screen.getByText("0 points")).toBeTruthy();
		expect(screen.getByText("Nothing is ready to start.")).toBeTruthy();
		// (The feature has no points at all, so it has no bar.)
		expect(screen.getByText("0 of 0 points")).toBeTruthy();
	});

	it("draws a board with no agents at all", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_agents") {
				return { items: [] };
			}
			return ANSWERS[name] as never;
		});
		render(<BoardPage path="board" params={{}} search={{}} ctx={fake.ctx} />);
		await waitFor(() => {
			expect(screen.getByText("No agents yet.")).toBeTruthy();
		});
	});

	it("keeps the state filter when only the state is written", async () => {
		const fake = drawn({ state: "", text: "" });
		await waitFor(() => {
			expect(screen.getByRole("combobox", { name: "State" })).toBeTruthy();
		});

		fireEvent.change(screen.getByRole("combobox", { name: "State" }), {
			target: { value: "queued" },
		});

		expect(fake.navigations).toEqual(["/ui/p/orch/board?state=queued"]);
	});

	it("clears a filter by clearing it, and asks the same page", async () => {
		const fake = drawn({ state: "queued", text: "inbox" });
		await waitFor(() => {
			expect(screen.getByRole("combobox", { name: "State" })).toBeTruthy();
		});

		fireEvent.change(screen.getByRole("searchbox", { name: "Text" }), {
			target: { value: "" },
		});

		expect(fake.navigations).toEqual(["/ui/p/orch/board?state=queued"]);
	});

	it("assigns the task carried onto an agent's column, then reads the board again", async () => {
		const fake = drawn();
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /deepseek-a/ })).toBeTruthy();
		});

		const features = within(screen.getByRole("region", { name: "Features" }));
		const card = features.getByRole("article", { name: /29\.5/ });
		fireEvent.dragStart(card);
		fireEvent.drop(screen.getByRole("region", { name: /deepseek-a/ }));

		await waitFor(() => {
			expect(fake.calls.filter((one) => one.name === "orch_assign")).toHaveLength(1);
		});
		expect(fake.calls.find((one) => one.name === "orch_assign")?.request).toEqual({
			key: "29.5",
			agent: "deepseek-a",
		});
		// (The board is read again, so what it drew is what udl now says.)
		await waitFor(() => {
			expect(fake.calls.filter((one) => one.name === "orch_list_features")).toHaveLength(2);
		});
		expect(fake.toasts).toEqual([]);
	});

	it("asks before stacking a suggestion on the task it needs, and does nothing when told no", async () => {
		const fake = drawn();
		fake.yes = false;
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /suggested/i })).toBeTruthy();
		});

		const suggested = within(screen.getByRole("region", { name: "Suggested" }));
		fireEvent.dragStart(suggested.getByRole("article", { name: /29\.5/ }));
		fireEvent.drop(screen.getByRole("region", { name: /deepseek-a/ }));

		await waitFor(() => {
			expect(fake.asked).toEqual(["Stack on #29.3?"]);
		});
		expect(fake.calls.filter((one) => one.name === "orch_assign")).toEqual([]);
	});

	it("stacks it when told yes, carrying the suggestion's task", async () => {
		const fake = drawn();
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /suggested/i })).toBeTruthy();
		});

		const suggested = within(screen.getByRole("region", { name: "Suggested" }));
		fireEvent.dragStart(suggested.getByRole("article", { name: /29\.5/ }));
		fireEvent.drop(screen.getByRole("region", { name: /deepseek-a/ }));

		await waitFor(() => {
			expect(fake.calls.filter((one) => one.name === "orch_assign")).toHaveLength(1);
		});
		expect(fake.calls.find((one) => one.name === "orch_assign")?.request).toEqual({
			key: "29.5",
			agent: "deepseek-a",
		});
	});

	it("says why a drop was refused, and reads the board again", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_assign") {
				throw new Error("29.5 is a person's task");
			}
			return ANSWERS[name] as never;
		});
		render(<BoardPage path="board" params={{}} search={{}} ctx={fake.ctx} />);
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /deepseek-a/ })).toBeTruthy();
		});

		const features = within(screen.getByRole("region", { name: "Features" }));
		fireEvent.dragStart(features.getByRole("article", { name: /29\.5/ }));
		fireEvent.drop(screen.getByRole("region", { name: /deepseek-a/ }));

		await waitFor(() => {
			expect(fake.toasts).toEqual([{ message: "29.5 is a person's task", kind: "error" }]);
		});
		await waitFor(() => {
			expect(fake.calls.filter((one) => one.name === "orch_list_features")).toHaveLength(2);
		});
	});

	it("takes a task back out of its queue when it is dropped on the features pane", async () => {
		const fake = drawn();
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /deepseek-a/ })).toBeTruthy();
		});

		const column = within(screen.getByRole("region", { name: "deepseek-a" }));
		fireEvent.dragStart(column.getByRole("listitem", { name: /29\.3/ }));
		fireEvent.drop(screen.getByRole("region", { name: "Features" }));

		await waitFor(() => {
			expect(fake.calls.filter((one) => one.name === "orch_unassign")).toHaveLength(1);
		});
		expect(fake.calls.find((one) => one.name === "orch_unassign")?.request).toEqual({
			key: "29.3",
		});
	});

	it("sets a queue to the order a task was dropped at", async () => {
		const fake = drawn();
		await waitFor(() => {
			expect(screen.getByRole("region", { name: /deepseek-a/ })).toBeTruthy();
		});

		const column = within(screen.getByRole("region", { name: "deepseek-a" }));
		fireEvent.dragStart(column.getByRole("listitem", { name: /29\.3/ }));
		fireEvent.drop(column.getByRole("listitem", { name: /29\.2/ }));

		await waitFor(() => {
			expect(fake.calls.filter((one) => one.name === "orch_reorder_queue")).toHaveLength(1);
		});
		// (Dropped on the top one: it goes above it.)
		expect(fake.calls.find((one) => one.name === "orch_reorder_queue")?.request).toEqual({
			agent: "deepseek-a",
			keys: ["29.3", "29.2"],
		});
	});

	it("assigns the same way from the menu, for a keyboard and a pointer alike", async () => {
		const fake = drawn();
		await waitFor(() => {
			expect(screen.getByRole("region", { name: "Features" })).toBeTruthy();
		});

		const features = within(screen.getByRole("region", { name: "Features" }));
		const card = features.getByRole("article", { name: /29\.5/ });
		fireEvent.click(within(card).getByRole("button", { name: /assign to/i }));
		fireEvent.click(screen.getByRole("menuitem", { name: "deepseek-a" }));

		await waitFor(() => {
			expect(fake.calls.filter((one) => one.name === "orch_assign")).toHaveLength(1);
		});
		expect(fake.calls.find((one) => one.name === "orch_assign")?.request).toEqual({
			key: "29.5",
			agent: "deepseek-a",
		});
	});
});
