import { render, screen } from "@testing-library/react";
import type { RouteProps } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { pages } from "./pages";
import { fakeCtx, settle } from "./testing/fake";

/** What a page is given: its route, what its `$name`s were, and the shell's context. */
function given(
	path: string,
	params: Record<string, string> = {},
): RouteProps & {
	ctx: ReturnType<typeof fakeCtx>["ctx"];
} {
	return { path, params, search: {}, ctx: fakeCtx(() => ({})).ctx };
}

describe("every route the manifest contributes", () => {
	it.each([
		["inbox", "Inbox"],
		["board", "Board"],
		["progress", "Progress"],
		["agents", "Agents"],
		["questions", "Q&A"],
		["merge", "Ready to merge"],
		["actions", "To action"],
	])("draws its heading: %s", (route, heading) => {
		const Page = pages[route];
		expect(Page).toBeDefined();
		if (Page === undefined) {
			return;
		}

		render(<Page {...given(route)} />);

		expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(heading);
	});

	it("draws a feature's heading, from its key", () => {
		const Page = pages["features/$key"];
		expect(Page).toBeDefined();
		if (Page === undefined) {
			return;
		}

		render(<Page {...given("features/29", { key: "29" })} />);

		expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Feature 29");
	});

	it("draws a task's heading, from its key", () => {
		const Page = pages["tasks/$key"];
		expect(Page).toBeDefined();
		if (Page === undefined) {
			return;
		}

		render(<Page {...given("tasks/29.2", { key: "29.2" })} />);

		expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Task 29.2");
	});

	it("draws an agent's heading, from its name", () => {
		const Page = pages["agents/$name"];
		expect(Page).toBeDefined();
		if (Page === undefined) {
			return;
		}

		render(<Page {...given("agents/deepseek-a", { name: "deepseek-a" })} />);

		expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("deepseek-a");
	});

	it("draws a page whose $name was not given, without it", () => {
		const Task = pages["tasks/$key"];
		const Feature = pages["features/$key"];
		const Question = pages["questions/$key"];
		const Agent = pages["agents/$name"];
		expect(Task && Feature && Question && Agent).toBeTruthy();
		if (
			Task === undefined ||
			Feature === undefined ||
			Question === undefined ||
			Agent === undefined
		) {
			return;
		}

		render(
			<>
				<Task {...given("tasks")} />
				<Feature {...given("features")} />
				<Question {...given("questions")} />
				<Agent {...given("agents")} />
			</>,
		);

		const headings = screen.getAllByRole("heading", { level: 1 }).map((one) => one.textContent);
		// (The question's page is 29.7's: it reads the question, and its heading is the page's.)
		expect(headings).toEqual(["Task ", "Feature ", "Q&A", "Agent"]);
	});

	it("says what failed, on the inbox and on the board", async () => {
		const fake = fakeCtx(() => {
			throw new Error("udl is not answering");
		});
		const Inbox = pages.inbox;
		const Board = pages.board;
		expect(Inbox && Board).toBeTruthy();
		if (Inbox === undefined || Board === undefined) {
			return;
		}

		render(
			<>
				<Inbox {...{ path: "inbox", params: {}, search: {}, ctx: fake.ctx }} />
				<Board {...{ path: "board", params: {}, search: {}, ctx: fake.ctx }} />
			</>,
		);
		await settle();

		expect(screen.getAllByText("udl is not answering")).toHaveLength(2);
	});

	it("says it is reading the inbox, until it is read", () => {
		const fake = fakeCtx(() => new Promise(() => {}));
		const Inbox = pages.inbox;
		expect(Inbox).toBeDefined();
		if (Inbox === undefined) {
			return;
		}

		render(<Inbox {...{ path: "inbox", params: {}, search: {}, ctx: fake.ctx }} />);

		expect(screen.getByText("Reading what waits on a person…")).toBeTruthy();
	});

	it("draws a question's thread, from its key", async () => {
		const Page = pages["questions/$key"];
		expect(Page).toBeDefined();
		if (Page === undefined) {
			return;
		}

		const fake = fakeCtx(() => ({
			question: {
				key: "Q-8",
				project: "udl",
				title: "Which colour?",
				text: "",
				asked_by: "deepseek-b",
				to: "humans",
				state: "open",
				task: null,
				options: [],
				blocking: false,
				answer: null,
				answered_by: null,
				outcome: null,
				escalated: false,
			},
			thread: [],
			tasks: [],
			features: [],
		}));

		render(<Page {...{ ...given("questions/Q-8", { key: "Q-8" }), ctx: fake.ctx }} />);
		await settle();

		expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Q&A");
		expect(screen.getByRole("heading", { level: 2, name: "Which colour?" })).toBeTruthy();
	});
});
