import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { AgentPage } from "./agent";

/** What udl answers for deepseek-a: working on 29.6, with a pull on it, a session, and a
 * transcript. */
const ANSWERS: Record<string, unknown> = {
	orch_get_agent: {
		agent: {
			name: "deepseek-a",
			title: "DeepSeek A",
			runner: "opencode",
			model: "deepseek-v4.1",
			host: "local",
			role: "implementer",
			manager: "boss",
			manages: { assign: false, answer: false, review: false, plan: false },
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
			ref: { runner: "opencode", id: "ses-1", cwd: "/work", host: "local" },
			state: "running",
			turns: 3,
			tokens_in: 1200,
			tokens_out: 300,
			cost: 0.42,
			last_text: "Working.",
		},
		queue: [
			{ key: "29.6", project: "udl", feature: "29", title: "The agents", state: "in_progress" },
			{ key: "29.12", project: "udl", feature: "29", title: "The chat", state: "queued" },
		],
		reports: [],
	},
	orch_list_agents: {
		items: [
			{ agent: { name: "boss", title: "Boss", role: "manager", manager: null }, status: "idle" },
			{
				agent: { name: "deepseek-a", title: "DeepSeek A", role: "implementer", manager: "boss" },
				status: "working",
			},
		],
	},
	orch_list_tasks: {
		project: "udl",
		tasks: [
			{
				key: "29.6",
				project: "udl",
				feature: "29",
				title: "The agents",
				state: "in_progress",
				size: "L",
			},
			{
				key: "29.12",
				project: "udl",
				feature: "29",
				title: "The chat",
				state: "queued",
				size: "M",
			},
		],
	},
	orch_merge_list: {
		project: "udl",
		ready: [],
		coming: [
			{
				task: "29.6",
				feature: "29",
				title: "The agents",
				number: 101,
				url: "https://github.com/yelrom0/udl/pull/101",
				sha: "abc",
				state: "running",
				blockers: ["CI is running"],
				merger: null,
			},
		],
		held: [],
		done: [],
	},
	orch_progress: {
		features: [],
		models: [{ model: "deepseek-v4.1", hours_per_point: { hours: 0.5, source: "model" } }],
	},
	orch_get_session: {
		id: "0b9a1f8e-0000-4000-8000-000000000001",
		agent: "deepseek-a",
		task: "29.6",
		ref: { runner: "opencode", id: "ses-1", cwd: "/work", host: "local" },
		state: "running",
		turns: 3,
		tokens_in: 1200,
		tokens_out: 300,
		cost: 0.42,
		last_text: "Working.",
		created_at: "2026-11-24T01:00:00Z",
		limited_until: null,
		total: 4,
		events: [
			{ turn: 1, at: "2026-11-24T01:00:01Z", event: { kind: "text_delta", text: "I am " } },
			{ turn: 1, at: "2026-11-24T01:00:02Z", event: { kind: "text_delta", text: "on it." } },
			{
				turn: 1,
				at: "2026-11-24T01:00:03Z",
				event: { kind: "tool_started", name: "bash", summary: "pytest -q" },
			},
			{
				turn: 1,
				at: "2026-11-24T01:00:04Z",
				event: { kind: "usage", input_tokens: 1200, output_tokens: 300, cost: "0.42" },
			},
		],
	},
	orch_send: {},
	orch_pause_agent: {},
	orch_resume_agent: {},
	orch_stop_session: {},
	orch_issue_token: { token: "udl_agent_abc" },
	orch_reorder_queue: {},
};

/** The page, drawn, once its read has answered. */
async function read() {
	await settle();
	await screen.findByRole("heading", { level: 1, name: "DeepSeek A" });
	// (The session is read once the agent's answer names it.)
	await settle();
}

describe("an agent's page (29.6)", () => {
	it("draws the agent: its name, its status, its role, what it runs on, and who it reports to", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		expect(screen.getByText("deepseek-a")).toBeTruthy();
		expect(screen.getByLabelText("working")).toBeTruthy();
		expect(screen.getByText("implementer")).toBeTruthy();
		expect(screen.getByText("opencode · deepseek-v4.1 · local")).toBeTruthy();
		expect(screen.getByText("reports to boss")).toBeTruthy();
		expect(screen.getByText("You build udl.")).toBeTruthy();
	});

	it("draws its queue in order, each task's size, and the current one's pull", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		const queue = within(screen.getByLabelText("Queue"));
		const now = within(queue.getByLabelText("29.6"));
		expect(now.getByText("now")).toBeTruthy();
		expect(now.getByText("L")).toBeTruthy();
		expect(now.getByRole("link", { name: "29.6" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/29.6",
		);
		expect(now.getByRole("link", { name: /pull|#101|101/ })).toBeTruthy();
		const next = within(queue.getByLabelText("29.12"));
		expect(next.getByText("2")).toBeTruthy();
		expect(next.getByText("M")).toBeTruthy();
		// (A pull is open on 29.6 only.)
		expect(next.queryByRole("link", { name: /pull|#101|101/ })).toBeNull();
	});

	it("says what the queue comes to: its points, and the hours they take at its model's rate", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		// (L and M are 5 and 3 points; its model takes half an hour a point.)
		expect(screen.getByText("8 points · about 4h")).toBeTruthy();
	});

	it("reorders the queue by a drag, writing the whole order", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.dragStart(within(screen.getByLabelText("Queue")).getByLabelText("29.12"));
		fireEvent.drop(within(screen.getByLabelText("Queue")).getByLabelText("29.6"));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_reorder_queue")?.request).toEqual({
			agent: "deepseek-a",
			keys: ["29.12", "29.6"],
			project: "udl",
		});
	});

	it("draws its session: what it has used, and its transcript as its runner said it", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		const session = within(screen.getByLabelText("Session"));
		expect(session.getByText("running")).toBeTruthy();
		expect(session.getByText("turn 3")).toBeTruthy();
		expect(session.getByText("1,200 in · 300 out")).toBeTruthy();
		expect(session.getByText("$0.42")).toBeTruthy();
		// (Two deltas of one turn are one paragraph; the tool and the usage stand alone.)
		const transcript = within(screen.getByLabelText("Transcript"));
		expect(transcript.getByText("I am on it.")).toBeTruthy();
		expect(transcript.getByText("▸ bash — pytest -q")).toBeTruthy();
		expect(transcript.getByText("1,200 in · 300 out · $0.42")).toBeTruthy();
	});

	it("says something to it, and reads its session again", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.change(screen.getByLabelText("Say something to it"), {
			target: { value: "How is it going?" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Send" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_send")?.request).toEqual({
			agent: "deepseek-a",
			text: "How is it going?",
		});
		expect((screen.getByLabelText("Say something to it") as HTMLTextAreaElement).value).toBe("");
	});

	it("says why when udl refuses what it was told", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_send") {
				throw new Error("it is not in a session");
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.change(screen.getByLabelText("Say something to it"), { target: { value: "Hello" } });
		fireEvent.click(screen.getByRole("button", { name: "Send" }));
		await settle();

		expect(fake.toasts).toEqual([{ message: "it is not in a session", kind: "error" }]);
	});

	it("pauses it, and resumes it when it is paused", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "Pause" }));
		await settle();
		expect(fake.calls.find((one) => one.name === "orch_pause_agent")?.request).toEqual({
			name: "deepseek-a",
		});
	});

	it("offers to resume one that is paused", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_agent") {
				const answer = ANSWERS.orch_get_agent as { agent: Record<string, unknown> };
				return { ...answer, agent: { ...answer.agent, paused: true } } as never;
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "Resume" }));
		await settle();
		expect(fake.calls.find((one) => one.name === "orch_resume_agent")?.request).toEqual({
			name: "deepseek-a",
		});
	});

	it("stops its session, naming udl's own id for it", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "Stop its session" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_stop_session")?.request).toEqual({
			session: "0b9a1f8e-0000-4000-8000-000000000001",
		});
	});

	it("gives it a token, and says it is shown once", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "Issue a token" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_issue_token")?.request).toEqual({
			agent: "deepseek-a",
		});
		expect(screen.getByText("udl_agent_abc")).toBeTruthy();
		expect(screen.getByText("Copy it now: it is shown once.")).toBeTruthy();
	});

	it("says it is not in a session when it is not", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_agent") {
				const answer = ANSWERS.orch_get_agent as { agent: Record<string, unknown> };
				return { ...answer, session: null } as never;
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		expect(screen.getByText("It is not in a session.")).toBeTruthy();
		expect(screen.queryByRole("button", { name: "Stop its session" })).toBeNull();
	});

	it("reads the session the page was opened at, not only its current one", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage
				ctx={fake.ctx}
				params={{ name: "deepseek-a" }}
				path="agents/$name"
				search={{ session: "0b9a1f8e-0000-4000-8000-000000000002" }}
			/>,
		);
		await read();

		expect(fake.calls.find((one) => one.name === "orch_get_session")?.request).toEqual({
			session: "0b9a1f8e-0000-4000-8000-000000000002",
		});
	});

	it("says what went wrong when the agent cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("no such agent");
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "nobody" }} path="agents/$name" search={{}} />,
		);
		await settle();

		expect(screen.getByText("no such agent")).toBeTruthy();
	});

	it("draws a manager's four powers, and says an implementer has none", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		expect(screen.getByText("It is not a manager: it has no powers.")).toBeTruthy();
	});

	it("draws what a manager may do, each as given", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_agent") {
				const answer = ANSWERS.orch_get_agent as { agent: Record<string, unknown> };
				return {
					...answer,
					agent: {
						...answer.agent,
						role: "manager",
						manages: { assign: true, answer: false, review: true, plan: false },
					},
				} as never;
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		expect(screen.getByText("✓ Assign")).toBeTruthy();
		expect(screen.getByText("— Answer")).toBeTruthy();
		expect(screen.getByText("✓ Review")).toBeTruthy();
		expect(screen.getByText("— Plan")).toBeTruthy();
	});
});

describe("an agent's page: the rest of what a person may do (29.6)", () => {
	it("edits it from its own page", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "Edit" }));
		await settle();
		const form = within(screen.getByRole("form", { name: "Edit deepseek-a" }));
		expect((form.getByLabelText("Brief") as HTMLTextAreaElement).value).toBe("You build udl.");
		fireEvent.change(form.getByLabelText("Title"), { target: { value: "DeepSeek A!" } });
		fireEvent.click(form.getByRole("button", { name: "Save" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_set_agent")?.request).toMatchObject({
			name: "deepseek-a",
			title: "DeepSeek A!",
		});
		expect(screen.queryByRole("form", { name: "Edit deepseek-a" })).toBeNull();
	});

	it("says why when a token cannot be given", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_issue_token") {
				throw new Error("only a person may give a token");
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "Issue a token" }));
		await settle();

		expect(fake.toasts).toEqual([{ message: "only a person may give a token", kind: "error" }]);
		expect(screen.queryByText("New token")).toBeNull();
	});

	it("says nothing to it when the box is empty", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fireEvent.change(screen.getByLabelText("Say something to it"), { target: { value: "   " } });
		fireEvent.click(screen.getByRole("button", { name: "Send" }));
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_send")).toEqual([]);
	});

	it("says its session's task and cost only when it has them", async () => {
		const fake = fakeCtx((name) => {
			// (The card follows the session's own read, so both answers say it has neither.)
			if (name === "orch_get_agent") {
				const answer = ANSWERS.orch_get_agent as { session: Record<string, unknown> };
				return {
					...(ANSWERS.orch_get_agent as Record<string, unknown>),
					session: { ...answer.session, cost: null, task: "" },
				} as never;
			}
			if (name === "orch_get_session") {
				return {
					...(ANSWERS.orch_get_session as Record<string, unknown>),
					cost: null,
					task: "",
				} as never;
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		const session = within(screen.getByLabelText("Session"));
		expect(session.queryByText(/^\$0\./)).toBeNull();
		expect(session.queryByText(/^on /)).toBeNull();
	});

	it("draws an agent with no status as idle", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_agent") {
				return { ...(ANSWERS.orch_get_agent as Record<string, unknown>), status: "" } as never;
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		expect(screen.getByLabelText("idle")).toBeTruthy();
	});
});

describe("an agent's page: the chain it sits in (29.6)", () => {
	it("says the whole way up, not just its manager's name", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_agents") {
				return {
					items: [
						{ agent: { name: "top", title: "Top", role: "manager", manager: null } },
						{ agent: { name: "boss", title: "Boss", role: "manager", manager: "top" } },
						{ agent: { name: "deepseek-a", role: "implementer", manager: "boss" } },
					],
				} as never;
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		expect(screen.getByText("reports to top › boss")).toBeTruthy();
	});
});

describe("an agent's page: its transcript as it streams (29.6)", () => {
	it("grows with each line its runner sends: the event names its session, and it is read again", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_session" && fake.calls.filter((one) => one.name === name).length > 1) {
				const answer = ANSWERS.orch_get_session as { events: unknown[] };
				return {
					...answer,
					events: [
						...answer.events,
						{ turn: 4, at: "2026-11-24T01:05:00Z", event: { kind: "text_delta", text: " Still " } },
					],
				} as never;
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage ctx={fake.ctx} params={{ name: "deepseek-a" }} path="agents/$name" search={{}} />,
		);
		await read();

		fake.emit("orch.session.event", {
			agent: "deepseek-a",
			session: "0b9a1f8e-0000-4000-8000-000000000001",
			event: { kind: "text_delta", text: " Still " },
			turn: 4,
		});
		await settle();

		expect(within(screen.getByLabelText("Transcript")).getByText(/Still/)).toBeTruthy();
		expect(fake.calls.filter((one) => one.name === "orch_get_session")).toHaveLength(2);
	});
});

describe("an agent's page: a session a task sent it to (29.6)", () => {
	it("shows that session, says it is not the current one, and offers no Send", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_get_session") {
				return {
					...(ANSWERS.orch_get_session as Record<string, unknown>),
					id: "0b9a1f8e-0000-4000-8000-0000000000ff",
					state: "ended",
					turns: 7,
				} as never;
			}
			return ANSWERS[name] as never;
		});
		render(
			<AgentPage
				ctx={fake.ctx}
				params={{ name: "deepseek-a" }}
				path="agents/$name"
				search={{ session: "0b9a1f8e-0000-4000-8000-0000000000ff" }}
			/>,
		);
		await read();

		expect(fake.calls.find((one) => one.name === "orch_get_session")?.request).toEqual({
			session: "0b9a1f8e-0000-4000-8000-0000000000ff",
		});
		const session = within(screen.getByLabelText("Session"));
		expect(session.getByText("ended")).toBeTruthy();
		expect(session.getByText("turn 7")).toBeTruthy();
		expect(session.getByText(/not its current one/)).toBeTruthy();
		expect(session.queryByRole("button", { name: "Send" })).toBeNull();
	});
});
