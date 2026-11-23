import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { JsonObject, JsonValue, RouteProps } from "@udl/ui-sdk";
import { describe, expect, it, vi } from "vitest";
import { fakeCtx } from "../testing/fake";
import { InboxPage } from "./inbox";

/** A question as `orch_inbox` sends one (28.1). */
function question(over: JsonObject = {}): JsonObject {
	return {
		key: "Q-8",
		title: "Should a task moved between agents keep its worktree?",
		text: "A task moved from deepseek-a to deepseek-b keeps its branch and its worktree.",
		asked_by: "deepseek-b",
		to: "humans",
		state: "open",
		task: "27.3",
		options: ["Keep it", "Make a new one"],
		blocking: true,
		...over,
	};
}

/** A merge row as `orch_inbox`'s merge list sends one (28.3). */
function row(number: number, over: JsonObject = {}): JsonObject {
	return {
		task: "26.4",
		feature: "26",
		title: "feat(orch): links",
		number,
		url: `https://github.com/yelrom0/udl/pull/${number}`,
		sha: "abc1234",
		state: "ready",
		blockers: [],
		merger: null,
		...over,
	};
}

/** The inbox as `orch_inbox` answers it (28.4), with 29.1's sample data. */
function inbox(over: JsonObject = {}): JsonObject {
	return {
		project: "udl",
		auto_merge: true,
		merger: "claude",
		merge: {
			project: "udl",
			auto_merge: true,
			merger: "claude",
			ready: [row(41)],
			coming: [row(42, { title: "feat(orch): agent tokens", task: "27.2" })],
			held: [row(40, { title: "the 1Password vault", task: "25.11", state: "held" })],
			done: [],
		},
		questions: [
			question(),
			question({
				key: "Q-9",
				title: "Which OpenRouter key should the opencode runner use?",
				text: "",
				asked_by: "lead",
				blocking: false,
				task: null,
				options: [],
			}),
		],
		actions: [
			{
				key: "A-3",
				title: "Create the 1Password service account",
				text: "",
				when: "now",
				after: null,
				task: null,
				assignee: "human:daniel",
			},
			{
				key: "A-4",
				title: "Rotate the OpenRouter key",
				text: "",
				when: "after",
				after: "25.3",
				task: null,
				assignee: "human:daniel",
			},
			{
				key: "A-5",
				title: "Add thevault's SSH key to the hosts list",
				text: "",
				when: "later",
				after: null,
				task: null,
				assignee: "human:daniel",
			},
		],
		agents: [
			{
				name: "deepseek-a",
				title: "DeepSeek A",
				role: "implementer",
				state: "working",
				task: "29.3",
				line: "working · 29.3 the inbox",
			},
			{
				name: "claude",
				title: "Claude",
				role: "manager",
				state: "idle",
				task: null,
				line: "idle · manager",
			},
		],
		updated_at: "2026-11-23T13:30:00Z",
		...over,
	};
}

/** A page given a context whose `orch_inbox` answers `answers` in turn, and its calls kept. */
function given(
	answers: (JsonValue | Promise<JsonValue>)[],
	calls?: JsonObject[],
): RouteProps & { ctx: ReturnType<typeof fakeCtx>["ctx"] } {
	let index = 0;
	const fake = fakeCtx((name, request) => {
		if (name === "orch_answer") {
			calls?.push(request);
			return {};
		}
		const answer = answers[Math.min(index, answers.length - 1)];
		index += 1;
		return answer ?? {};
	});
	return { path: "inbox", params: {}, search: {}, ctx: fake.ctx };
}

/** The page, rendered, and read once. */
async function drawn(answers: (JsonValue | Promise<JsonValue>)[] = [inbox()]): Promise<void> {
	render(<InboxPage {...given(answers)} />);
	await waitFor(() => {
		expect(screen.queryByText("Reading what waits on a person…")).toBeNull();
	});
}

/** The text of a card, by the name it is given. */
function card(name: string): HTMLElement {
	return screen.getByRole("region", { name });
}

describe("the inbox (29.3)", () => {
	it("says what it is, and the three counts with what is behind each", async () => {
		await drawn();

		expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Inbox");
		// (Each count: its own number, and the line that says what the number is made of.)
		const ready = screen.getByRole("group", { name: "Ready to merge" });
		expect(within(ready).getByText("1")).toBeTruthy();
		expect(ready.textContent).toContain("1 coming soon · 1 held");
		const asked = screen.getByRole("group", { name: "Open questions" });
		expect(within(asked).getByText("2")).toBeTruthy();
		expect(asked.textContent).toContain("1 blocking");
		const doing = screen.getByRole("group", { name: "To action" });
		expect(within(doing).getByText("3")).toBeTruthy();
		expect(doing.textContent).toContain("1 now · 1 after · 1 later");
	});

	it("pins a blocking question on top, with its options as one-click answers", async () => {
		await drawn();

		const pinned = within(card("Blocking"));
		expect(pinned.getByText("Should a task moved between agents keep its worktree?")).toBeTruthy();
		expect(pinned.getByText("asked by deepseek-b")).toBeTruthy();
		expect(pinned.getByText("Q-8")).toBeTruthy();
		// (Its task, as the key that is always a link.)
		expect(pinned.getByRole("link", { name: "27.3" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/27.3",
		);
		expect((pinned.getByRole("button", { name: "Keep it" }) as HTMLButtonElement).disabled).toBe(
			false,
		);
		expect(
			(pinned.getByRole("button", { name: "Make a new one" }) as HTMLButtonElement).disabled,
		).toBe(false);
	});

	it("answers a blocking question in one click, and it leaves", async () => {
		const calls: JsonObject[] = [];
		// (The second read is the inbox after the answer: the question is no longer open.)
		const after = inbox({
			questions: [
				question({
					key: "Q-9",
					title: "Which OpenRouter key should the opencode runner use?",
					text: "",
					asked_by: "lead",
					blocking: false,
					task: null,
					options: [],
				}),
			],
		});
		render(<InboxPage {...given([inbox(), after], calls)} />);
		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Keep it" })).toBeTruthy();
		});

		fireEvent.click(screen.getByRole("button", { name: "Keep it" }));

		await waitFor(() => {
			expect(screen.queryByRole("button", { name: "Keep it" })).toBeNull();
		});
		expect(calls).toEqual([{ project: "udl", key: "Q-8", answer: "Keep it" }]);
		expect(screen.queryByText("Should a task moved between agents keep its worktree?")).toBeNull();
	});

	it("says an answer is on its way, and takes no second click", async () => {
		let settleAnswer: (value: JsonValue) => void = () => {};
		const held = new Promise<JsonValue>((resolve) => {
			settleAnswer = resolve;
		});
		let reads = 0;
		const fake = fakeCtx((name) => {
			if (name === "orch_answer") {
				return held;
			}
			reads += 1;
			return reads === 1 ? inbox() : inbox({ questions: [] });
		});
		render(<InboxPage path="inbox" params={{}} search={{}} ctx={fake.ctx} />);
		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Keep it" })).toBeTruthy();
		});

		fireEvent.click(screen.getByRole("button", { name: "Keep it" }));

		await waitFor(() => {
			expect(screen.getByText("Answering…")).toBeTruthy();
		});
		expect((screen.getByRole("button", { name: "Keep it" }) as HTMLButtonElement).disabled).toBe(
			true,
		);
		expect(fake.calls.filter((one) => one.name === "orch_answer")).toHaveLength(1);
		settleAnswer({});
		await waitFor(() => {
			expect(screen.queryByText("Answering…")).toBeNull();
		});
	});

	it("takes no second click while the inbox is being read again", async () => {
		// (The answer landed, and the read that says so is still out: the question is still drawn,
		// and its button is still "Answering…", so a second click cannot get through.)
		let settleRead: (value: JsonValue) => void = () => {};
		const held = new Promise<JsonValue>((resolve) => {
			settleRead = resolve;
		});
		let reads = 0;
		const fake = fakeCtx((name) => {
			if (name === "orch_answer") {
				return {};
			}
			reads += 1;
			return reads === 1 ? inbox() : held;
		});
		render(<InboxPage path="inbox" params={{}} search={{}} ctx={fake.ctx} />);
		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Keep it" })).toBeTruthy();
		});

		fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
		await waitFor(() => {
			expect(screen.getByText("Answering…")).toBeTruthy();
		});

		// (The option's own button is still there, still the one that was clicked, and disabled.)
		const keep = screen.getByRole("button", { name: "Keep it" }) as HTMLButtonElement;
		expect(keep.disabled).toBe(true);
		fireEvent.click(keep);
		expect(fake.calls.filter((one) => one.name === "orch_answer")).toHaveLength(1);

		settleRead(inbox({ questions: [] }));
		await waitFor(() => {
			expect(screen.queryByText("Answering…")).toBeNull();
		});
	});

	it("keeps each question's own answer in flight: one finishing leaves the other waiting", async () => {
		const settle = new Map<string, (value: JsonValue) => void>();
		const held = new Map<string, Promise<JsonValue>>();
		for (const key of ["Q-8", "Q-11"]) {
			held.set(
				key,
				new Promise<JsonValue>((resolve) => {
					settle.set(key, resolve);
				}),
			);
		}
		const two = inbox({
			questions: [question(), question({ key: "Q-11", title: "And this one?", options: ["Yes"] })],
		});
		const fake = fakeCtx((name, request) => {
			if (name === "orch_answer") {
				return held.get(String(request.key)) ?? {};
			}
			return two;
		});
		render(<InboxPage path="inbox" params={{}} search={{}} ctx={fake.ctx} />);
		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Keep it" })).toBeTruthy();
		});

		fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
		fireEvent.click(screen.getByRole("button", { name: "Yes" }));
		await waitFor(() => {
			expect(screen.getAllByText("Answering…")).toHaveLength(2);
		});

		// (The first one's answer lands and its read finishes: the second is still on its way.)
		settle.get("Q-8")?.({});
		await waitFor(() => {
			expect(screen.getAllByText("Answering…")).toHaveLength(1);
		});
		expect((screen.getByRole("button", { name: "Yes" }) as HTMLButtonElement).disabled).toBe(true);

		settle.get("Q-11")?.({});
		await waitFor(() => {
			expect(screen.queryByText("Answering…")).toBeNull();
		});
	});

	it("keeps the question when udl refuses the answer, and says why", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_answer") {
				return Promise.reject(new Error("Q-8 is closed: it was answered at 12:00"));
			}
			return inbox();
		});
		render(<InboxPage path="inbox" params={{}} search={{}} ctx={fake.ctx} />);
		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Keep it" })).toBeTruthy();
		});

		fireEvent.click(screen.getByRole("button", { name: "Keep it" }));

		await waitFor(() => {
			expect(screen.getByRole("alert").textContent).toContain(
				"Q-8 is closed: it was answered at 12:00",
			);
		});
		expect((screen.getByRole("button", { name: "Keep it" }) as HTMLButtonElement).disabled).toBe(
			false,
		);
	});

	it("opens the thread of a question that offers no options", async () => {
		await drawn();

		const thread = screen.getByRole("link", {
			name: "Which OpenRouter key should the opencode runner use?",
		});
		expect(thread.getAttribute("href")).toBe("/ui/p/orch/questions/Q-9");
	});

	it("shows what each agent is doing: its dot, its name and the task it is on", async () => {
		await drawn();

		const strip = within(card("Agents"));
		expect(strip.getByText("deepseek-a")).toBeTruthy();
		expect(strip.getByText("working · 29.3 the inbox")).toBeTruthy();
		// (Its task is a link to the task's own page.)
		expect(strip.getByRole("link", { name: "29.3" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/29.3",
		);
		expect(strip.getByRole("img", { name: "working" })).toBeTruthy();
		expect(strip.getByRole("img", { name: "idle" })).toBeTruthy();
	});

	it("says whether auto merge is on and who the merger is, and links every PR (Q-8)", async () => {
		await drawn();

		const merge = within(card("Ready to merge"));
		expect(merge.getByText("auto merge: claude")).toBeTruthy();
		// (The chip's own name is its number, its checks' dot and the bot's mark.)
		const pull = merge.getByRole("link", { name: /#41/ });
		expect(pull.getAttribute("href")).toBe("https://github.com/yelrom0/udl/pull/41");
		expect(pull.getAttribute("target")).toBe("_blank");
		expect(pull.getAttribute("rel")).toBe("noopener noreferrer");
		// (What is coming and what is held are in the same card, each with its own reason.)
		expect(merge.getByText("feat(orch): agent tokens")).toBeTruthy();
		expect(merge.getByText("the 1Password vault")).toBeTruthy();
	});

	it("says auto merge is off when it is, and no merger", async () => {
		await drawn([
			inbox({
				auto_merge: false,
				merger: null,
				merge: {
					project: "udl",
					auto_merge: false,
					merger: null,
					ready: [],
					coming: [],
					held: [],
					done: [],
				},
			}),
		]);

		const merge = within(card("Ready to merge"));
		expect(merge.getByText("auto merge: off")).toBeTruthy();
		expect(merge.getByText("Nothing is ready to merge.")).toBeTruthy();
	});

	it("groups what only a person can do: now, after, later", async () => {
		await drawn();

		const actions = within(card("To action"));
		expect(actions.getByText("Create the 1Password service account")).toBeTruthy();
		expect(actions.getByText("Now")).toBeTruthy();
		expect(actions.getByText("After")).toBeTruthy();
		expect(actions.getByText("Later")).toBeTruthy();
		// (An action that waits on a task says which one, as a link.)
		expect(actions.getByRole("link", { name: "25.3" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/25.3",
		);
	});

	it("says what reading it failed with", async () => {
		const fake = fakeCtx(() => Promise.reject(new Error("udl is not answering")));
		render(<InboxPage path="inbox" params={{}} search={{}} ctx={fake.ctx} />);

		await waitFor(() => {
			expect(screen.getByRole("alert").textContent).toContain("udl is not answering");
		});
	});

	it("draws an empty inbox as an empty one: nothing to merge, ask or do, and no strip", async () => {
		await drawn([
			inbox({
				questions: [],
				actions: [],
				agents: [],
				merge: {
					project: "udl",
					auto_merge: false,
					merger: null,
					ready: [],
					coming: [],
					held: [],
					done: [],
				},
			}),
		]);

		expect(within(card("Ready to merge")).getByText("Nothing is ready to merge.")).toBeTruthy();
		expect(within(card("Questions")).getByText("Nothing is being asked.")).toBeTruthy();
		expect(within(card("To action")).getByText("Nothing needs a person.")).toBeTruthy();
		expect(screen.queryByRole("region", { name: "Agents" })).toBeNull();
		expect(screen.queryByRole("region", { name: "Blocking" })).toBeNull();
		expect(screen.getByRole("group", { name: "Open questions" }).textContent).toContain(
			"none blocking",
		);
	});

	it("says nothing about who asked a question that says nothing and is about nothing", async () => {
		await drawn([
			inbox({
				questions: [
					question({
						key: "Q-10",
						title: "Anything?",
						text: "",
						asked_by: "",
						task: null,
						blocking: false,
						options: [],
					}),
				],
			}),
		]);

		expect(screen.queryByText(/asked by/)).toBeNull();
		expect(within(card("Questions")).getByText("Anything?")).toBeTruthy();
	});

	it("falls back to an agent's title and an idle dot when it says nothing else", async () => {
		await drawn([
			inbox({
				agents: [{ name: "lead", title: "Lead", role: "manager", state: "", task: null, line: "" }],
			}),
		]);

		const strip = within(card("Agents"));
		expect(strip.getByText("Lead")).toBeTruthy();
		expect(strip.getByRole("img", { name: "idle" })).toBeTruthy();
	});

	it("says when it last read, so a page left open is not mistaken for news", async () => {
		vi.useFakeTimers();
		try {
			const now = Date.now();
			render(<InboxPage {...given([inbox({ updated_at: new Date(now).toISOString() })])} />);
			// (The store reads on a microtask, not a timer.)
			await act(async () => {});
			expect(screen.getByText("Updated 0 s ago")).toBeTruthy();

			// (It ticks: two minutes later the line says two minutes, with no read in between.)
			await act(async () => {
				await vi.advanceTimersByTimeAsync(120_000);
			});

			expect(screen.getByText("Updated 2 min ago")).toBeTruthy();
		} finally {
			vi.useRealTimers();
		}
	});
});
