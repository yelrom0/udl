import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mergeListOf, type QueueEntry, type Task } from "../model";
import { fakeCtx, settle } from "../testing/fake";
import { Queue } from "./queue";

/** The merge list of a project with one pull open, on 29.6. */
const MERGE = mergeListOf({
	project: "udl",
	ready: [
		{
			task: "29.6",
			feature: "29",
			title: "The agents",
			number: 101,
			url: "https://github.com/yelrom0/udl/pull/101",
			sha: "abc",
			state: "running",
			blockers: [],
			merger: null,
		},
	],
	coming: [],
	held: [],
	done: [],
});

/** A task, as `orch_list_tasks` sends it (the parts this page reads are the ones it is given). */
function task(over: Partial<Task> & Pick<Task, "key" | "title">): Task {
	return {
		project: "udl",
		feature: "29",
		state: "queued",
		assignee: "deepseek-a",
		size: null,
		needs: [],
		spec: "",
		hold: null,
		blocked_on: null,
		repo: "",
		branch: "",
		worktree: "",
		task_kind: "feature",
		...over,
	};
}

/** One entry of a queue, in its place. */
function entry(over: Partial<QueueEntry> & Pick<QueueEntry, "key" | "title">): QueueEntry {
	return { project: "udl", feature: "29", state: "queued", position: 0, ...over };
}

/** The queue of deepseek-a: 29.6 (L) and 29.12 (M). */
const ENTRIES = [
	entry({ key: "29.6", title: "The agents", state: "in_progress" }),
	entry({ key: "29.12", title: "The chat", position: 1 }),
];

/** The tasks the entries name, with their sizes. */
const TASKS = [
	task({ key: "29.6", title: "The agents", state: "in_progress", size: "L" }),
	task({ key: "29.12", title: "The chat", size: "M" }),
];

describe("an agent's queue (29.6)", () => {
	it("says nothing is queued when nothing is", () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={[]}
				hoursPerPoint={0.5}
				merge={MERGE}
				refresh={async () => {}}
				tasks={[]}
			/>,
		);

		expect(screen.getByText("Nothing is queued for it.")).toBeTruthy();
	});

	it("says what the queue comes to, and about how long at its rate", () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={ENTRIES}
				hoursPerPoint={0.5}
				merge={MERGE}
				refresh={async () => {}}
				tasks={TASKS}
			/>,
		);

		expect(screen.getByText("8 points · about 4h")).toBeTruthy();
	});

	it("rounds the minutes once: eight points at 1.999 hours is sixteen hours, not 15h 60m", () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={ENTRIES}
				// (15.992 hours: 959.52 minutes, which rounds to 960 — the old sum said 15h 60m.)
				hoursPerPoint={1.999}
				merge={MERGE}
				refresh={async () => {}}
				tasks={TASKS}
			/>,
		);

		expect(screen.getByText("8 points · about 16h")).toBeTruthy();
	});

	it("says its points alone when no rate is known", () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={ENTRIES}
				hoursPerPoint={null}
				merge={MERGE}
				refresh={async () => {}}
				tasks={TASKS}
			/>,
		);

		expect(screen.getByText("8 points")).toBeTruthy();
	});

	it("says a single point as one, and the minutes of a part of an hour", () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={[ENTRIES[0] as QueueEntry]}
				hoursPerPoint={0.25}
				merge={MERGE}
				refresh={async () => {}}
				tasks={[task({ key: "29.6", title: "The agents", state: "in_progress", size: "S" })]}
			/>,
		);

		// (An S is one point; a quarter of an hour each is fifteen minutes.)
		expect(screen.getByText("1 point · about 15m")).toBeTruthy();
	});

	it("counts no points for a task that has no size", () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={ENTRIES}
				hoursPerPoint={0.5}
				merge={MERGE}
				refresh={async () => {}}
				tasks={[]}
			/>,
		);

		expect(screen.getByText("0 points · about 0m")).toBeTruthy();
	});

	it("writes the whole order when a task is dropped on another", async () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={ENTRIES}
				hoursPerPoint={0.5}
				merge={MERGE}
				refresh={async () => {}}
				tasks={TASKS}
			/>,
		);

		const queue = within(screen.getByLabelText("Queue"));
		fireEvent.dragStart(queue.getByLabelText("29.12"));
		fireEvent.drop(queue.getByLabelText("29.6"));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_reorder_queue")?.request).toEqual({
			agent: "deepseek-a",
			keys: ["29.12", "29.6"],
			project: "udl",
		});
	});

	it("does nothing when a task is dropped on itself", async () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={ENTRIES}
				hoursPerPoint={0.5}
				merge={MERGE}
				refresh={async () => {}}
				tasks={TASKS}
			/>,
		);

		const queue = within(screen.getByLabelText("Queue"));
		fireEvent.dragStart(queue.getByLabelText("29.6"));
		fireEvent.drop(queue.getByLabelText("29.6"));
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_reorder_queue")).toEqual([]);
	});

	it("leaves another agent's drag alone, and a drop with nothing carried", async () => {
		const fake = fakeCtx(() => ({}));
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={ENTRIES}
				hoursPerPoint={0.5}
				merge={MERGE}
				refresh={async () => {}}
				tasks={TASKS}
			/>,
		);

		const queue = within(screen.getByLabelText("Queue"));
		// (Nothing carried at all: a drop from outside this queue.)
		fireEvent.drop(queue.getByLabelText("29.6"));
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_reorder_queue")).toEqual([]);
	});

	it("says why when udl refuses the order, and reads it again", async () => {
		const fake = fakeCtx(() => {
			throw new Error("29.6 is not queued for deepseek-a");
		});
		let read = 0;
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={ENTRIES}
				hoursPerPoint={0.5}
				merge={MERGE}
				refresh={async () => {
					read += 1;
				}}
				tasks={TASKS}
			/>,
		);

		const queue = within(screen.getByLabelText("Queue"));
		fireEvent.dragStart(queue.getByLabelText("29.12"));
		fireEvent.drop(queue.getByLabelText("29.6"));
		await settle();

		expect(fake.toasts).toEqual([{ message: "29.6 is not queued for deepseek-a", kind: "error" }]);
		expect(read).toBe(1);
	});
});

describe("a queue that spans two projects (29.6)", () => {
	it("reorders within the project the drop is in, and says so about the other", async () => {
		const fake = fakeCtx(() => ({}));
		const other = entry({ key: "other.1", project: "other", title: "Elsewhere", position: 2 });
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={[...ENTRIES, other]}
				hoursPerPoint={0.5}
				merge={MERGE}
				refresh={async () => {}}
				tasks={TASKS}
			/>,
		);

		const queue = within(screen.getByLabelText("Queue"));
		// (A drop onto a task of another project is not a reorder udl can make.)
		fireEvent.dragStart(queue.getByLabelText("other.1"));
		fireEvent.drop(queue.getByLabelText("29.6"));
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_reorder_queue")).toEqual([]);
		expect(fake.toasts).toEqual([
			{
				message: "A queue is reordered within its project: those two tasks are not in one.",
				kind: "error",
			},
		]);
	});

	it("counts a size from the project its task is in, not the other one's", () => {
		const fake = fakeCtx(() => ({}));
		// (Both projects have a task called 29.6; only the udl one is the L.)
		const elsewhere = task({ key: "29.6", project: "other", title: "Another 29.6", size: "S" });
		render(
			<Queue
				agent="deepseek-a"
				ctx={fake.ctx}
				entries={[entry({ key: "29.6", title: "The agents" })]}
				hoursPerPoint={null}
				merge={MERGE}
				refresh={async () => {}}
				tasks={[TASKS[0] as Task, elsewhere]}
			/>,
		);

		expect(screen.getByText("5 points")).toBeTruthy();
	});
});
