import { describe, expect, it } from "vitest";
import { asSize, boardOf, inboxOf, mergeListOf, POINTS, sessionOf } from "./model";

describe("reading an answer", () => {
	it("reads an inbox, and nothing at all from nothing", () => {
		const nothing = inboxOf(undefined);

		expect(nothing.project).toBe("");
		expect(nothing.merge.ready).toEqual([]);
		expect(nothing.questions).toEqual([]);
		expect(nothing.actions).toEqual([]);
		expect(nothing.agents).toEqual([]);
		expect(nothing.updated_at).toBe("");
	});

	it("reads every field of an answer that is all there", () => {
		const inbox = inboxOf({
			project: "udl",
			merge: {
				project: "udl",
				auto_merge: true,
				merger: "claude",
				ready: [
					{
						task: "29.2",
						feature: "29",
						title: "The skeleton",
						number: 78,
						url: "u",
						sha: "s",
						state: "ready",
						blockers: [],
						merger: "claude",
					},
				],
				coming: [],
				held: [],
				done: [],
			},
			questions: [
				{ key: "Q-8", text: "t", to: "humans", state: "open", task: "29.2", options: ["a"] },
			],
			actions: [{ key: "A-1", title: "t", when: "now", after: "A-2" }],
			agents: [
				{
					name: "a",
					title: "a",
					role: "implementer",
					state: "working",
					task: "29.2",
					line: "a — implementing 29.2",
				},
			],
			updated_at: "2026-11-23T00:00:00Z",
		});

		expect(inbox.merge.ready[0]?.merger).toBe("claude");
		expect(inbox.questions[0]?.task).toBe("29.2");
		expect(inbox.actions[0]?.after).toBe("A-2");
		expect(inbox.agents[0]?.task).toBe("29.2");
		expect(inbox.agents[0]?.line).toBe("a — implementing 29.2");
	});

	it("keeps a field only when it is the type it should be", () => {
		const inbox = inboxOf({
			project: 7,
			merge: { auto_merge: "yes", merger: 3, ready: [{}] },
			questions: [{ key: "Q-8", task: 12, options: ["a", 2] }],
			actions: [{ key: "A-1", after: false }],
			agents: [{ name: "deepseek-a", task: null }],
		});

		expect(inbox.project).toBe("");
		expect(inbox.merge.auto_merge).toBe(false);
		expect(inbox.merge.merger).toBeNull();
		expect(inbox.merge.ready).toHaveLength(1);
		expect(inbox.merge.ready[0]?.number).toBe(0);
		expect(inbox.questions[0]?.task).toBeNull();
		expect(inbox.questions[0]?.options).toEqual(["a", ""]);
		expect(inbox.actions[0]?.after).toBeNull();
		expect(inbox.agents[0]?.task).toBeNull();
		expect(inbox.agents[0]?.line).toBe("");
	});

	it("reads a merge list's four parts, each a list of rows", () => {
		const list = mergeListOf({
			project: "udl",
			auto_merge: true,
			merger: "claude",
			ready: [{ task: "29.2", number: 78, blockers: ["a", "b"] }],
			coming: [{ task: "29.3" }],
			held: [],
			done: [{ task: "28.7" }],
		});

		expect(list.auto_merge).toBe(true);
		expect(list.merger).toBe("claude");
		expect(list.ready[0]?.number).toBe(78);
		expect(list.ready[0]?.blockers).toEqual(["a", "b"]);
		expect(list.coming.map((row) => row.task)).toEqual(["29.3"]);
		expect(list.done.map((row) => row.task)).toEqual(["28.7"]);
	});

	it("reads the board's four lists, and says nothing when they are not lists", () => {
		const board = boardOf(
			{ features: [{ key: "29", title: "Screens", status: "active" }] },
			{ tasks: [{ key: "29.2", size: "L", assignee: "deepseek-a", needs: ["28.7"] }] },
			{
				items: [{ agent: { name: "deepseek-a", title: "a", role: "implementer" }, status: "idle" }],
			},
			{ suggestions: [{ task: "29.3", agent: "deepseek-a", reasons: ["free"] }] },
		);

		expect(board.features[0]?.status).toBe("active");
		expect(board.tasks[0]?.size).toBe("L");
		expect(board.tasks[0]?.assignee).toBe("deepseek-a");
		expect(board.tasks[0]?.needs).toEqual(["28.7"]);
		expect(board.agents[0]?.name).toBe("deepseek-a");
		expect(board.agents[0]?.status).toBe("idle");
		expect(board.suggestions[0]?.reasons).toEqual(["free"]);

		const empty = boardOf(undefined, { tasks: "nope" }, undefined, undefined);
		expect(empty.tasks).toEqual([]);
		expect(empty.agents).toEqual([]);
	});

	it("reads each agent's queue and who reports to it (29.4)", () => {
		const board = boardOf(
			{ project: "udl", features: [] },
			{ project: "udl", tasks: [] },
			{
				items: [
					{
						agent: { name: "claude", title: "Claude", role: "manager", model: "m-1" },
						status: "working",
						queue: [
							{ key: "29.4", feature: "29", title: "Board", state: "in_progress", position: 0 },
							{ key: "29.5", feature: "29", title: "Pages", state: "queued", position: 1 },
						],
						reports: ["deepseek-a", "deepseek-b"],
					},
				],
			},
			{ project: "udl", suggestions: [] },
		);

		expect(board.agents[0]?.model).toBe("m-1");
		expect(board.agents[0]?.queue.map((one) => one.key)).toEqual(["29.4", "29.5"]);
		expect(board.agents[0]?.queue[1]?.position).toBe(1);
		expect(board.agents[0]?.reports).toEqual(["deepseek-a", "deepseek-b"]);
	});

	it("reads a suggestion's score and what it would stack on (29.4)", () => {
		const board = boardOf(undefined, undefined, undefined, {
			project: "udl",
			suggestions: [{ task: "29.5", agent: "a", score: 7, reasons: ["free"], stack_on: "29.4" }],
		});

		expect(board.suggestions[0]?.score).toBe(7);
		expect(board.suggestions[0]?.stack_on).toBe("29.4");
	});

	it("knows the four sizes and their points, and nothing else as a size", () => {
		expect(POINTS).toEqual({ S: 1, M: 3, L: 5, XL: 8 });
		expect(asSize("XL")).toBe("XL");
		expect(asSize("XXL")).toBeNull();
		expect(asSize(null)).toBeNull();
	});

	it("reads a session's fields, and a task's own fields when it has none", () => {
		expect(sessionOf({ id: "ses_1", turns: 3, task: 5 }).task).toBe("");
		expect(sessionOf(undefined).turns).toBe(0);
	});
});
