import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { TaskPage } from "./task";

/** What udl answers for 29.2: a ready task with a pull, a note, a question and a session. */
const ANSWERS: Record<string, unknown> = {
	orch_get_task: {
		feature: {
			key: "29",
			project: "udl",
			title: "Screens and the orch UI plugin",
			status: "active",
		},
		task: {
			key: "29.2",
			feature: "29",
			title: "The UI plugin's skeleton",
			state: "ready",
			spec: "## What it is\n\nA skeleton.",
			size: "L",
			needs: ["28.7"],
			assignee: "deepseek-a",
			repo: "yelrom0/udl",
			branch: "feat/29.2-ui-skeleton",
		},
		links: [
			{
				link_kind: "pull",
				url: "https://github.com/yelrom0/udl/pull/80",
				title: "The skeleton",
				state: { checks: "pass", review: "approved" },
			},
		],
		notes: [{ at: "2026-11-24T01:00:00Z", author: "claude", note_kind: "review", text: "Clean." }],
		questions: [{ key: "Q-8", title: "Screens", state: "answered", task: "29.2" }],
	},
	orch_estimate: { size: "L", estimate: "PT4H", worked: "PT1H20M" },
	orch_readiness: {
		ready: false,
		held: false,
		blockers: ["CI is red"],
		pending: ["ci"],
		open_items: [{ number: "1", status: "open", text: "a nit in docs" }],
		waited: 42,
	},
	orch_list_tasks: {
		project: "udl",
		tasks: [
			{ key: "29.2", feature: "29", state: "ready", needs: [] },
			{ key: "29.5", feature: "29", state: "open", needs: ["29.2"] },
		],
	},
	orch_list_agents: {
		items: [
			{
				agent: { name: "deepseek-a" },
				status: "working",
				session: { agent: "deepseek-a", task: "29.2", ref: { id: "s-1" }, state: "running" },
			},
		],
	},
};

/** The page, drawn, once its read has answered. */
async function read() {
	await settle();
	await screen.findByRole("heading", { level: 1, name: "Task 29.2" });
}

describe("a task's page (29.5)", () => {
	it("draws the task: its title, its state, its spec, its size and its estimate", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		expect(screen.getByText("The UI plugin's skeleton")).toBeTruthy();
		expect(screen.getByText("ready")).toBeTruthy();
		expect(screen.getByRole("heading", { name: "What it is" })).toBeTruthy();
		// (Its size, what that is expected to take, and what it has taken.)
		expect(screen.getByText("4h expected")).toBeTruthy();
		expect(screen.getByText("1h 20m worked")).toBeTruthy();
	});

	it("reaches its feature, and back", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		const back = screen.getByRole("link", { name: /Feature 29/ });
		expect(back.getAttribute("href")).toBe("/ui/p/orch/features/29");
		expect(back.textContent).toContain("Screens and the orch UI plugin");
	});

	it("links its assignee, its needs, what it unblocks, its questions and its session", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		expect(screen.getByRole("link", { name: "deepseek-a" }).getAttribute("href")).toBe(
			"/ui/p/orch/agents/deepseek-a",
		);
		const needs = screen.getByRole("list", { name: "Needs" });
		expect(within(needs).getByRole("link", { name: "28.7" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/28.7",
		);
		const unblocks = screen.getByRole("list", { name: "Unblocks" });
		expect(within(unblocks).getByRole("link", { name: "29.5" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/29.5",
		);
		expect(screen.getByRole("link", { name: "Q-8" }).getAttribute("href")).toBe(
			"/ui/p/orch/questions/Q-8",
		);
		// (The agent's page, at that session.)
		expect(screen.getByRole("link", { name: /session/i }).getAttribute("href")).toBe(
			"/ui/p/orch/agents/deepseek-a?session=s-1",
		);
	});

	it("draws the pull's readiness, with its blockers and the bot's items", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		const pull = screen.getByRole("region", { name: "Pull" });
		expect(within(pull).getByRole("link", { name: /#80/ })).toBeTruthy();
		expect(within(pull).getByText("CI is red")).toBeTruthy();
		expect(within(pull).getByText("a nit in docs")).toBeTruthy();
		expect(within(pull).getByText("Not ready")).toBeTruthy();
	});

	it("moves the task to another state, and reads it again", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();
		const before = fake.calls.filter((one) => one.name === "orch_get_task").length;

		const move = screen.getByRole("combobox", { name: "Move to" });
		// (Every state but the one it is in: a person may move a task anywhere.)
		expect(within(move).queryByRole("option", { name: "ready" })).toBeNull();
		fireEvent.change(move, { target: { value: "in_progress" } });
		fireEvent.click(screen.getByRole("button", { name: "Move" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_move_task")?.request).toEqual({
			key: "29.2",
			project: "udl",
			to: "in_progress",
		});
		expect(fake.calls.filter((one) => one.name === "orch_get_task").length).toBe(before + 1);
	});

	it("leaves a person's name as a name: only an agent has a page", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_task") {
				return {
					...(ANSWERS.orch_get_task as object),
					task: {
						key: "29.2",
						feature: "29",
						title: "Held",
						state: "blocked",
						assignee: "human:owner",
					},
				};
			}
			return ANSWERS[name] as never;
		});
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		expect(screen.getByText("human:owner")).toBeTruthy();
		expect(screen.queryByRole("link", { name: "human:owner" })).toBeNull();
	});

	it("says why it is held", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_task") {
				return {
					...(ANSWERS.orch_get_task as object),
					task: {
						key: "29.2",
						feature: "29",
						title: "Held",
						state: "ready",
						hold: "built now, merged later",
					},
				};
			}
			return ANSWERS[name] as never;
		});
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		expect(screen.getByText("Held: built now, merged later")).toBeTruthy();
	});

	it("offers the states it may move to now, after a move", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		const { rerender } = render(
			<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />,
		);
		await read();

		fireEvent.change(screen.getByRole("combobox", { name: "Move to" }), {
			target: { value: "blocked" },
		});
		expect((screen.getByRole("combobox", { name: "Move to" }) as HTMLSelectElement).value).toBe(
			"blocked",
		);

		// (The task moves on, and "blocked" is the state it is in now — not a move it may make.)
		const moved = fakeCtx((name) => {
			if (name === "orch_get_task") {
				return {
					...(ANSWERS.orch_get_task as object),
					task: { key: "29.2", feature: "29", title: "Moved", state: "blocked" },
				};
			}
			return ANSWERS[name] as never;
		});
		rerender(<TaskPage ctx={moved.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await settle();

		const box = screen.getByRole("combobox", { name: "Move to" }) as HTMLSelectElement;
		expect([...box.options].map((one) => one.value)).not.toContain("blocked");
		expect(box.value).toBe("open");
	});

	it("adds a note to the task's timeline", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		fireEvent.change(screen.getByRole("textbox", { name: "Note" }), {
			target: { value: "The bot is happy." },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add note" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_add_note")?.request).toEqual({
			owner_kind: "task",
			owner: "29.2",
			project: "udl",
			note_kind: "progress",
			text: "The bot is happy.",
		});
	});

	it("says what udl answered when the task cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no such task: '99.9'");
		});
		render(<TaskPage ctx={fake.ctx} params={{ key: "99.9" }} path="tasks/$key" search={{}} />);

		expect(await screen.findByText(/no such task/)).toBeTruthy();
	});
});

describe("a task's page's quiet edges (29.5)", () => {
	it("says so when it has no feature, nobody holds it, no pull and no session", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_task") {
				return {
					feature: null,
					task: { key: "29.9", title: "Alone", state: "open" },
					links: [],
					notes: [],
					questions: [],
				};
			}
			if (name === "orch_readiness") {
				return { ready: false, blockers: [], open_items: [], pending: [] };
			}
			if (name === "orch_list_agents") {
				return { items: [] };
			}
			return { size: null, estimate: null, worked: "" };
		});
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.9" }} path="tasks/$key" search={{}} />);
		await screen.findByRole("heading", { level: 1, name: "Task 29.9" });
		await settle();

		expect(screen.getByText("No feature")).toBeTruthy();
		expect(screen.getByText("Nobody.")).toBeTruthy();
		expect(screen.getByText("No pull yet.")).toBeTruthy();
		expect(screen.getByText("No session right now.")).toBeTruthy();
		expect(screen.getByText("Nothing: it waits on no other task.")).toBeTruthy();
		expect(screen.getByText("No questions about it.")).toBeTruthy();
		expect(screen.getByText("No spec yet.")).toBeTruthy();
		expect(screen.getByText("No notes yet.")).toBeTruthy();
	});

	it("says a pull is ready, or held, when udl does", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_readiness") {
				return { ready: true, held: true, blockers: [], open_items: [] };
			}
			return ANSWERS[name] as never;
		});
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		const pull = screen.getByRole("region", { name: "Pull" });
		expect(within(pull).getByText("Ready to merge")).toBeTruthy();
		expect(within(pull).getByText("Held: built now, merged later.")).toBeTruthy();
	});

	it("says what udl answered when it refuses a move", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_move_task") {
				throw new Error("29.2 cannot move from 'ready' to 'done' by you");
			}
			return ANSWERS[name] as never;
		});
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		fireEvent.change(screen.getByRole("combobox", { name: "Move to" }), {
			target: { value: "done" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Move" }));
		await settle();

		// (A refusal is said in a toast, and the page reads the task again.)
		expect(fake.toasts[0]?.message).toContain("cannot move");
		expect(fake.toasts[0]?.kind).toBe("error");
	});

	it("adds a link to the task", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		fireEvent.change(screen.getByRole("textbox", { name: "URL" }), {
			target: { value: "https://github.com/yelrom0/udl/commit/abc1234" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add link" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_add_link")?.request).toEqual({
			owner_kind: "task",
			owner: "29.2",
			project: "udl",
			link_kind: "pull",
			url: "https://github.com/yelrom0/udl/commit/abc1234",
		});
	});
});

describe("a task's page when its pull's readiness is unknown (29.5)", () => {
	it("says so beside the pull rather than dropping the page", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_readiness") {
				throw new Error("No github account is connected");
			}
			return ANSWERS[name] as never;
		});
		render(<TaskPage ctx={fake.ctx} params={{ key: "29.2" }} path="tasks/$key" search={{}} />);
		await read();

		const pull = screen.getByRole("region", { name: "Pull" });
		expect(within(pull).getByText("Whether it may be merged is not known right now.")).toBeTruthy();
		expect(within(pull).getByRole("link", { name: /#80/ })).toBeTruthy();
	});
});
