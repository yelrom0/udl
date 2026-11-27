import { describe, expect, it } from "vitest";
import {
	agentInfoOf,
	agentsOf,
	asSize,
	boardOf,
	chainOf,
	chatOf,
	chatsOf,
	durationOf,
	estimateOf,
	featureInfoOf,
	inboxOf,
	isAbove,
	isQuestionKey,
	machinesOf,
	mergeListOf,
	movesFrom,
	POINTS,
	progressReportOf,
	readinessOf,
	reportOf,
	runnerProblemsOf,
	runnersOf,
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
						session: { id: "s-1", agent: "deepseek-a", task: "29.2", state: "running" },
					},
					{
						agent: { name: "ernie-d" },
						status: "working",
						session: { id: "s-2", agent: "ernie-d", task: "29.7", state: "running" },
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

describe("an agent's page (29.6)", () => {
	const ANSWER = {
		agent: {
			name: "deepseek-a",
			title: "DeepSeek A",
			runner: "opencode",
			runner_options: { profile: "paid" },
			model: "deepseek-v4.1-flash",
			host: "local",
			role: "implementer",
			manager: "boss",
			manages: { assign: true, answer: false, review: true, plan: false },
			projects: ["udl"],
			brief: "You build udl.",
			max_nudges: 4,
			enabled: true,
			paused: false,
		},
		status: "working",
		session: {
			id: "0b9a1f8e-0000-4000-8000-000000000001",
			agent: "deepseek-a",
			task: "29.6",
			ref: { runner: "opencode", id: "ses-1", cwd: "/tmp", host: "local" },
			state: "running",
			turns: 3,
			tokens_in: 1200,
			tokens_out: 300,
			cost: 0.42,
			last_text: "Working on it.",
		},
		queue: [
			{ key: "29.6", project: "udl", feature: "29", title: "The agents", state: "in_progress" },
			{ key: "29.12", project: "udl", feature: "29", title: "The chat", state: "queued" },
		],
		reports: ["deepseek-b"],
	};

	it("reads an agent with its session, its queue and its reports", () => {
		const info = agentInfoOf(ANSWER);

		expect(info.agent.name).toBe("deepseek-a");
		expect(info.agent.runner).toBe("opencode");
		expect(info.agent.model).toBe("deepseek-v4.1-flash");
		expect(info.agent.host).toBe("local");
		expect(info.agent.role).toBe("implementer");
		expect(info.agent.manager).toBe("boss");
		expect(info.agent.manages).toEqual({ assign: true, answer: false, review: true, plan: false });
		expect(info.agent.projects).toEqual(["udl"]);
		expect(info.agent.brief).toBe("You build udl.");
		expect(info.agent.max_nudges).toBe(4);
		expect(info.status).toBe("working");
		expect(info.session).toEqual({
			id: "0b9a1f8e-0000-4000-8000-000000000001",
			task: "29.6",
			state: "running",
			turns: 3,
			tokens_in: 1200,
			tokens_out: 300,
			cost: 0.42,
			last_text: "Working on it.",
		});
		expect(info.queue.map((one) => one.key)).toEqual(["29.6", "29.12"]);
		expect(info.queue[0]?.project).toBe("udl");
		expect(info.reports).toEqual(["deepseek-b"]);
	});

	it("reads an agent that reports to the humans and has nothing running", () => {
		const info = agentInfoOf({
			agent: { name: "solo", title: "Solo", role: "manager", manager: null },
			status: "idle",
			session: null,
			queue: [],
			reports: [],
		});

		expect(info.agent.manager).toBeNull();
		expect(info.session).toBeNull();
		expect(info.agent.manages).toEqual({
			assign: false,
			answer: false,
			review: false,
			plan: false,
		});
		expect(info.agent.projects).toEqual([]);
		expect(info.agent.enabled).toBe(true);
		expect(info.agent.paused).toBe(false);
	});

	it("reads nothing at all from nothing", () => {
		const info = agentInfoOf(undefined);

		expect(info.agent.name).toBe("");
		expect(info.agent.runner).toBe("");
		expect(info.status).toBe("");
		expect(info.session).toBeNull();
		expect(info.queue).toEqual([]);
		expect(info.reports).toEqual([]);
	});

	it("reads the agents a project has, by name", () => {
		expect(agentsOf({ items: [ANSWER, ANSWER] }).map((one) => one.agent.name)).toEqual([
			"deepseek-a",
			"deepseek-a",
		]);
		expect(agentsOf(undefined)).toEqual([]);
	});

	it("reads a session's transcript, each thing it said as it said it", () => {
		const session = sessionOf({
			id: "ses-1",
			agent: "deepseek-a",
			task: "29.6",
			ref: { runner: "opencode", id: "ses-1", cwd: "/tmp", host: "local" },
			state: "idle",
			turns: 2,
			tokens_in: 900,
			tokens_out: 120,
			cost: "0.1",
			last_text: "Done.",
			created_at: "2026-11-24T01:00:00Z",
			limited_until: null,
			total: 5,
			events: [
				{ turn: 1, at: "2026-11-24T01:00:01Z", event: { kind: "text_delta", text: "Hello" } },
				{
					turn: 1,
					at: "2026-11-24T01:00:02Z",
					event: { kind: "tool_started", name: "bash", summary: "pytest -q" },
				},
				{
					turn: 1,
					at: "2026-11-24T01:00:03Z",
					event: { kind: "tool_finished", name: "bash", ok: true },
				},
				{
					turn: 2,
					at: "2026-11-24T01:00:04Z",
					event: { kind: "usage", input_tokens: 900, output_tokens: 120, cost: "0.1" },
				},
				{
					turn: 2,
					at: "2026-11-24T01:00:05Z",
					event: { kind: "turn_ended", reason: "done", text: "Done." },
				},
				{
					turn: 3,
					at: "2026-11-24T01:00:06Z",
					event: {
						kind: "runner_limited",
						message: "Free tier used up.",
						until: "2026-11-25T00:00:00Z",
					},
				},
				{
					turn: 4,
					at: "2026-11-24T01:00:07Z",
					event: { kind: "runner_failed", message: "It crashed." },
				},
			],
		});

		expect(session.id).toBe("ses-1");
		expect(session.total).toBe(5);
		expect(session.created_at).toBe("2026-11-24T01:00:00Z");
		expect(session.events.map((one) => one.kind)).toEqual([
			"text_delta",
			"tool_started",
			"tool_finished",
			"usage",
			"turn_ended",
			"runner_limited",
			"runner_failed",
		]);
		expect(session.events[0]).toMatchObject({ turn: 1, text: "Hello" });
		expect(session.events[1]).toMatchObject({ name: "bash", summary: "pytest -q" });
		expect(session.events[2]).toMatchObject({ ok: true });
		expect(session.events[3]).toMatchObject({ tokens_in: 900, tokens_out: 120, cost: 0.1 });
		expect(session.events[4]).toMatchObject({ reason: "done", text: "Done." });
		// (A cost is a Decimal, so udl sends it as a string: read it as one.)
		expect(session.events[3]?.cost).toBe(0.1);
		expect(session.cost).toBe(0.1);
		// (What a runner said went wrong, and when a quota is back, are their own fields.)
		expect(session.events[5]).toMatchObject({
			message: "Free tier used up.",
			until: "2026-11-25T00:00:00Z",
		});
		expect(session.events[6]).toMatchObject({ message: "It crashed.", until: null });
	});

	it("reads a session that has not started yet as one with nothing said", () => {
		const session = sessionOf(undefined);

		expect(session.id).toBe("");
		expect(session.events).toEqual([]);
		expect(session.cost).toBeNull();
		expect(session.limited_until).toBeNull();
	});

	it("reads the harnesses a host has, and what each can run", () => {
		const runners = runnersOf({
			items: [
				{
					host: "local",
					runner: "opencode",
					health: { installed: true, signed_in: true, version: "1.2.3" },
					models: [{ id: "deepseek-v4.1", name: "DeepSeek V4.1" }],
				},
				{ host: "local", runner: "claude-code", health: { installed: false } },
			],
			problems: ["jeff-wiggle/codex: it did not answer"],
		});

		expect(runners.map((one) => one.runner)).toEqual(["opencode", "claude-code"]);
		expect(runners[0]).toMatchObject({
			host: "local",
			installed: true,
			version: "1.2.3",
		});
		expect(runners[0]?.models).toEqual([{ id: "deepseek-v4.1", name: "DeepSeek V4.1" }]);
		expect(runners[1]?.installed).toBe(false);
		expect(runnersOf(undefined)).toEqual([]);
	});

	it("walks up the tree from an agent to the humans", () => {
		const tree = agentsOf({
			items: [
				{ agent: { name: "deepseek-a", role: "implementer", manager: "boss" }, status: "idle" },
				{ agent: { name: "boss", role: "manager", manager: null }, status: "idle" },
			],
		});

		expect(chainOf(tree, "deepseek-a")).toEqual(["boss"]);
		expect(chainOf(tree, "boss")).toEqual([]);
		expect(chainOf(tree, "nobody")).toEqual([]);
		expect(isAbove(tree, "boss", "deepseek-a")).toBe(true);
		expect(isAbove(tree, "deepseek-a", "boss")).toBe(false);
	});
});

describe("a chat", () => {
	it("reads a chat, its permission and its transcript", () => {
		const chat = chatOf({
			id: "0b9a1f8e-0000-0000-0000-000000000001",
			owner: "human:claude",
			runner: "claude-code",
			model: "claude-opus-5-5",
			permission_mode: "manual",
			host: "local",
			directory: "/work/udl",
			state: "idle",
			title: "why is the queue stuck?",
			turns: 1,
			tokens_in: 1200,
			tokens_out: 300,
			cost: "0.42",
			last_error: "",
			last_text: "Hello.",
			pending: 1,
			created_at: "2026-11-24T10:00:00Z",
			updated_at: "2026-11-24T10:05:00Z",
			permission: {
				id: "7c1e0000-0000-0000-0000-000000000002",
				tool: "Bash",
				input: { command: "git rebase origin/main" },
				state: "pending",
				message: "",
				allowed_for_chat: false,
				asked_at: "2026-11-24T10:04:00Z",
			},
			events: [{ kind: "said", text: "why is the queue stuck?" }],
			total: 1,
		});

		expect(chat.id).toBe("0b9a1f8e-0000-0000-0000-000000000001");
		expect(chat.runner).toBe("claude-code");
		expect(chat.model).toBe("claude-opus-5-5");
		expect(chat.cost).toBe(0.42);
		expect(chat.pending).toBe(1);
		expect(chat.permission?.tool).toBe("Bash");
		expect(chat.permission?.input).toEqual({ command: "git rebase origin/main" });
		expect(chat.events.map((one) => one.kind)).toEqual(["said"]);
	});

	it("reads a chat with nothing waiting on a person", () => {
		const chat = chatOf({ id: "one", state: "running" });

		expect(chat.permission).toBeNull();
		expect(chat.cost).toBeNull();
		expect(chat.events).toEqual([]);
		expect(chat.title).toBe("");
	});

	it("reads no chat at all from nothing", () => {
		const chat = chatOf(undefined);

		expect(chat.id).toBe("");
		expect(chat.state).toBe("");
		expect(chat.permission).toBeNull();
		expect(chatsOf(undefined)).toEqual([]);
	});

	it("reads the chats a person has, most recent first as udl sends them", () => {
		const chats = chatsOf({
			items: [
				{ id: "b", title: "the second", state: "idle" },
				{ id: "a", title: "the first", state: "ended" },
			],
		});

		expect(chats.map((one) => one.id)).toEqual(["b", "a"]);
	});
});

describe("the machines (29.9)", () => {
	const answered = {
		items: [
			{
				host: "local",
				runner: "claude-code",
				health: {
					installed: true,
					version: "3.2.1",
					latest: "3.2.1",
					signed_in: true,
					plan: "Max",
					guards: true,
				},
			},
			{
				host: "local",
				runner: "opencode",
				health: { installed: true, version: "2.1.2", latest: "2.1.2", signed_in: false },
			},
			{
				host: "thevault",
				runner: "claude-code",
				health: {
					installed: true,
					version: "3.1.9",
					latest: "3.2.1",
					signed_in: true,
					account: "someone@example.com",
					limited_until: "2026-11-28T12:00:00Z",
					problems: ["the plan is at its limit"],
				},
			},
		],
		problems: ["thevault/opencode: it did not answer"],
	};

	it("reads nothing at all from nothing", () => {
		expect(machinesOf(undefined)).toEqual([]);
	});

	it("puts each host's harnesses together, in the order udl answered", () => {
		const machines = machinesOf(answered);

		expect(machines.map((one) => one.host)).toEqual(["local", "thevault"]);
		expect(machines[0]?.harnesses.map((one) => one.runner)).toEqual(["claude-code", "opencode"]);
		expect(machines[1]?.harnesses.map((one) => one.runner)).toEqual(["claude-code"]);
	});

	it("says when a harness is a version behind", () => {
		const [local, thevault] = machinesOf(answered);

		expect(local?.harnesses[0]?.update).toBeNull();
		expect(thevault?.harnesses[0]?.update).toBe("3.2.1");
	});

	it("says who it is signed in as, and on what plan", () => {
		const [local, thevault] = machinesOf(answered);

		expect(local?.harnesses[0]?.signed_in).toBe(true);
		expect(local?.harnesses[0]?.plan).toBe("Max");
		expect(local?.harnesses[1]?.signed_in).toBe(false);
		expect(thevault?.harnesses[0]?.account).toBe("someone@example.com");
	});

	it("carries what is wrong with it, and when its quota is back", () => {
		const harness = machinesOf(answered)[1]?.harnesses[0];

		expect(harness?.limited_until).toBe("2026-11-28T12:00:00Z");
		expect(harness?.problems).toEqual(["the plan is at its limit"]);
		expect(harness?.guards).toBe(false);
	});

	it("keeps what could not be asked, as udl said it", () => {
		expect(machinesOf(answered).flatMap((one) => one.harnesses)).toHaveLength(3);
		expect(runnerProblemsOf(answered)).toEqual(["thevault/opencode: it did not answer"]);
	});
});

describe("the import's report (29.9)", () => {
	it("reads nothing at all from nothing", () => {
		const report = reportOf(undefined);

		expect(report.created).toEqual([]);
		expect(report.updated).toEqual([]);
		expect(report.unchanged).toEqual([]);
		expect(report.kept).toEqual([]);
		expect(report.unplaced).toEqual([]);
	});

	it("reads what it made, changed, kept and could not place", () => {
		const report = reportOf({
			created: ["29.1", "29.2"],
			updated: ["29.3"],
			unchanged: ["29.4"],
			kept: ["29.5"],
			unplaced: [
				{ source: "planning.md", line: 8122, text: "- [ ] 29.9 something", why: "no phase" },
			],
		});

		expect(report.created).toEqual(["29.1", "29.2"]);
		expect(report.updated).toEqual(["29.3"]);
		expect(report.unchanged).toEqual(["29.4"]);
		expect(report.kept).toEqual(["29.5"]);
		expect(report.unplaced).toEqual([
			{ source: "planning.md", line: 8122, text: "- [ ] 29.9 something", why: "no phase" },
		]);
	});
});

describe("reading a project's progress (29.11)", () => {
	it("reads the burn-up, the forecast, the features, the models and the tasks merged lately", () => {
		const progress = progressReportOf({
			project: "udl",
			series: [
				{ day: "2026-11-13", points: 236, merged: 0 },
				{ day: "2026-11-14", points: 236, merged: 3 },
			],
			points: 236,
			merged: 148,
			pace: 20.5,
			forecast: {
				likely: "2026-11-27T00:00:00Z",
				early: "2026-11-25T00:00:00Z",
				late: "2026-11-29T00:00:00Z",
				unsized: 2,
			},
			features: [
				{
					key: "29",
					title: "Screens",
					points: 26,
					merged: 8,
					forecast: {
						likely: "2026-11-27T00:00:00Z",
						early: "2026-11-25T00:00:00Z",
						late: "2026-11-29T00:00:00Z",
					},
				},
				{ key: "30", title: "Docs", points: 12, merged: 12, forecast: null },
			],
			models: [
				{
					model: "deepseek-v4.1-flash",
					runner: "opencode",
					tasks: 41,
					hours_per_point: { hours: 0.9, source: "model" },
					hours_per_size: { S: 0.8, M: 2.7, L: 4.4, XL: 7.0 },
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
			],
			estimate_ratio: 1.16,
		});

		expect(progress.project).toBe("udl");
		expect(progress.series).toEqual([
			{ day: "2026-11-13", points: 236, merged: 0 },
			{ day: "2026-11-14", points: 236, merged: 3 },
		]);
		expect(progress.points).toBe(236);
		expect(progress.merged).toBe(148);
		expect(progress.pace).toBe(20.5);
		expect(progress.forecast).toEqual({
			likely: "2026-11-27T00:00:00Z",
			early: "2026-11-25T00:00:00Z",
			late: "2026-11-29T00:00:00Z",
			unsized: 2,
		});
		expect(progress.features.map((one) => one.key)).toEqual(["29", "30"]);
		expect(progress.features[1]?.forecast).toBeNull();
		expect(progress.models[0]).toEqual({
			model: "deepseek-v4.1-flash",
			runner: "opencode",
			tasks: 41,
			hours_per_point: 0.9,
			rate_source: "model",
			hours_per_size: { S: 0.8, M: 2.7, L: 4.4, XL: 7.0 },
		});
		expect(progress.recent[0]?.estimate).toBe("PT4H24M");
		expect(progress.estimate_ratio).toBe(1.16);
	});

	it("reads nothing at all from nothing, and a forecast that says nothing", () => {
		const nothing = progressReportOf(undefined);

		expect(nothing.project).toBe("");
		expect(nothing.points).toBe(0);
		expect(nothing.merged).toBe(0);
		expect(nothing.series).toEqual([]);
		expect(nothing.features).toEqual([]);
		expect(nothing.models).toEqual([]);
		expect(nothing.recent).toEqual([]);
		expect(nothing.forecast.likely).toBe("");
		expect(nothing.forecast.unsized).toBe(0);
	});
});
