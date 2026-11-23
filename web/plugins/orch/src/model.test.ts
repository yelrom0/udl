import { describe, expect, it } from "vitest";
import {
	asSize,
	boardOf,
	durationOf,
	estimateOf,
	featureInfoOf,
	inboxOf,
	isQuestionKey,
	mergeListOf,
	movesFrom,
	POINTS,
	readinessOf,
	sessionOf,
	sessionOfTask,
	taskInfoOf,
} from "./model";

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

describe("a feature's and a task's pages (29.5)", () => {
	it("reads a feature with its spec, its totals, its tasks, its links and its notes", () => {
		const info = featureInfoOf({
			// (A feature's spec is its own: `orch_get_feature` sends it on the feature.)
			feature: { key: "29", title: "Screens", status: "active", spec: "## What it is" },
			totals: { points: 13, points_merged: 5, points_ready: 3 },
			tasks: [{ key: "29.2", title: "The skeleton", state: "merged", size: "L" }],
			links: [{ link_kind: "pull", url: "https://github.com/o/r/pull/80", title: "80", state: {} }],
			notes: [{ at: "2026-11-24T01:00:00Z", author: "deepseek-a", note_kind: "note", text: "hi" }],
		});

		expect(info.feature.key).toBe("29");
		expect(info.spec).toBe("## What it is");
		expect(info.totals.points).toBe(13);
		expect(info.totals.points_merged).toBe(5);
		expect(info.tasks[0]?.state).toBe("merged");
		expect(info.links[0]?.link_kind).toBe("pull");
		expect(info.notes[0]?.author).toBe("deepseek-a");
	});

	it("reads a task with its feature, its links, its notes, its questions and what it unblocks", () => {
		const info = taskInfoOf(
			{
				feature: { key: "29", title: "Screens", status: "active" },
				task: {
					key: "29.2",
					feature: "29",
					title: "The skeleton",
					state: "ready",
					spec: "## It",
					size: "L",
					needs: ["28.7"],
					assignee: "deepseek-a",
					repo: "yelrom0/udl",
					branch: "feat/29.2-ui-skeleton",
					blocked_on: "CI",
					hold: "built now, merged later",
				},
				links: [{ link_kind: "pull", url: "https://github.com/o/r/pull/80" }],
				notes: [{ at: "2026-11-24T01:00:00Z", author: "claude", note_kind: "note", text: "ok" }],
				questions: [{ key: "Q-8", title: "Screens", state: "open", task: "29.2" }],
			},
			["29.5"],
		);

		expect(info.task.key).toBe("29.2");
		expect(info.task.spec).toBe("## It");
		expect(info.task.branch).toBe("feat/29.2-ui-skeleton");
		expect(info.task.blocked_on).toBe("CI");
		// (A hold is why it is held, not a flag.)
		expect(info.task.hold).toBe("built now, merged later");
		expect(info.feature?.key).toBe("29");
		expect(info.notes[0]?.text).toBe("ok");
		expect(info.questions[0]?.key).toBe("Q-8");
		expect(info.unblocks).toEqual(["29.5"]);
	});

	it("reads nothing at all from nothing, and no feature from a task without one", () => {
		const nothing = featureInfoOf(undefined);
		expect(nothing.feature.key).toBe("");
		expect(nothing.totals.points).toBe(0);
		expect(nothing.tasks).toEqual([]);
		expect(nothing.notes).toEqual([]);

		const alone = taskInfoOf({ task: { key: "29.9" } }, []);
		expect(alone.feature).toBeNull();
		expect(alone.task.key).toBe("29.9");
		expect(alone.task.needs).toEqual([]);
	});

	it("reads an estimate, and a readiness with its blockers", () => {
		const estimate = estimateOf({ size: "L", estimate: "4h", worked: "1h 20m" });
		expect(estimate.size).toBe("L");
		expect(estimate.estimate).toBe("4h");
		expect(estimate.worked).toBe("1h 20m");

		const readiness = readinessOf({
			ready: false,
			held: true,
			blockers: ["CI is red", "the bot has 2 items"],
			pending: ["the bot"],
			open_items: [{ number: "1", status: "open", text: "a nit" }],
			waited: 42,
		});
		expect(readiness.ready).toBe(false);
		expect(readiness.held).toBe(true);
		expect(readiness.blockers).toEqual(["CI is red", "the bot has 2 items"]);
		expect(readiness.open_items).toEqual([{ number: "1", status: "open", text: "a nit" }]);
		expect(readiness.waited).toBe(42);

		expect(estimateOf(undefined).size).toBeNull();
		expect(readinessOf(undefined).blockers).toEqual([]);
	});

	it("tells a question's key from a line of text", () => {
		// (What a task waits on is the question's key, `Q-<n>`, or free text.)
		expect(isQuestionKey("Q-8")).toBe(true);
		expect(isQuestionKey("Q-108")).toBe(true);
		expect(isQuestionKey("CI")).toBe(false);
		expect(isQuestionKey("Q-8 and Q-9")).toBe(false);
		expect(isQuestionKey("")).toBe(false);
	});

	it("reads a duration as a person reads it, and anything else as it came", () => {
		// (What `orch_estimate` sends: an ISO 8601 duration.)
		expect(durationOf("PT4H")).toBe("4h");
		expect(durationOf("PT1H20M")).toBe("1h 20m");
		expect(durationOf("P1DT2H30M")).toBe("1d 2h 30m");
		expect(durationOf("PT0S")).toBe("0s");
		expect(durationOf("P")).toBe("0m");
		expect(durationOf("4h")).toBe("4h");
		expect(durationOf("")).toBe("");
	});

	it("finds the session a task is in, among the agents' (29.6's page takes it)", () => {
		const session = sessionOfTask(
			{
				items: [
					{
						agent: { name: "deepseek-a" },
						status: "working",
						session: { agent: "deepseek-a", task: "29.2", ref: { id: "s-1" }, state: "running" },
					},
					{
						agent: { name: "ernie-d" },
						status: "working",
						session: { agent: "ernie-d", task: "29.7", ref: { id: "s-2" }, state: "running" },
					},
				],
			},
			"29.7",
		);

		expect(session).toEqual({ agent: "ernie-d", id: "s-2", state: "running" });
		expect(sessionOfTask({ items: [] }, "29.2")).toBeNull();
	});

	it("lists the states a person may move a task to: every one but its own", () => {
		expect(movesFrom("open")).not.toContain("open");
		expect(movesFrom("open")).toContain("in_progress");
		expect(movesFrom("open")).toContain("cancelled");
		expect(movesFrom("merged")).toContain("done");
		expect(movesFrom("done")).toContain("merged");
	});
});
