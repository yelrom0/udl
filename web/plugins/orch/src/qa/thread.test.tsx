import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { JsonObject } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { QuestionPageView } from "./page";

/** A message on a thread, as `orch_get_question` sends it. */
function message(text: string, author: string): JsonObject {
	return { text, author, at: "2026-11-21T10:00:00Z" };
}

/** One question with everything about it, as `orch_get_question` answers it (28.1). */
function found(over: JsonObject = {}): JsonObject {
	return {
		question: {
			key: "Q-8",
			project: "udl",
			title: "Should a task moved between agents keep its worktree?",
			text: "A task moved from deepseek-a to deepseek-b keeps its branch and its worktree.",
			asked_by: "deepseek-b",
			to: "humans",
			state: "open",
			task: "27.3",
			options: ["Keep it", "Make a new one"],
			blocking: true,
			answer: null,
			answered_by: null,
			outcome: null,
			escalated: false,
		},
		thread: [message("It keeps its branch and its worktree.", "deepseek-b")],
		tasks: ["27.3"],
		features: [],
		...over,
	};
}

/** The props the page is drawn with. */
const props = { ctx: null as never, questionKey: "Q-8" };

/** A page on a fake context, drawn and read. */
async function draw(answer: (name: string, request: JsonObject) => JsonObject) {
	const fake = fakeCtx(answer);
	await act(async () => {
		render(<QuestionPageView {...props} ctx={fake.ctx} />);
		await settle();
	});
	return fake;
}

describe("a question's thread (29.7)", () => {
	it("says what was asked, what it is about, and what has been said", async () => {
		await draw(() => found());

		expect(
			screen.getByRole("heading", {
				name: "Should a task moved between agents keep its worktree?",
			}),
		).toBeTruthy();
		// (What it is about: the task it names, and the task it was asked in.)
		const about = screen.getByText("about");
		expect(about.parentElement?.textContent).toContain("27.3");
		expect(screen.getByText(/asked by deepseek-b/)).toBeTruthy();
		// (And what has been said, with who said it.)
		expect(screen.getByText("deepseek-b")).toBeTruthy();
		expect(screen.getByText("It keeps its branch and its worktree.")).toBeTruthy();
	});

	it("offers its options, and answers with the one clicked", async () => {
		const fake = await draw(() => found());

		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
			await settle();
		});

		const called = fake.calls.find((one) => one.name === "orch_answer");
		expect(called?.request).toEqual({ project: "udl", key: "Q-8", answer: "Keep it" });
	});

	it("shows the answer once it has one, and who gave it", async () => {
		await draw(() =>
			found({
				question: {
					key: "Q-8",
					project: "udl",
					title: "Which colour?",
					text: "",
					asked_by: "deepseek-b",
					to: "humans",
					state: "answered",
					task: null,
					options: [],
					blocking: false,
					answer: "The first one",
					answered_by: "human:claude",
					outcome: null,
					escalated: false,
				},
			}),
		);

		const answer = screen.getByRole("region", { name: "Answer" });
		expect(within(answer).getByText("Answered by human:claude")).toBeTruthy();
		expect(within(answer).getByText("The first one")).toBeTruthy();
		// (Answered: it offers no buttons.)
		expect(screen.queryByRole("button", { name: "Keep it" })).toBeNull();
	});

	it("adds a follow-up to the thread, and reads it again", async () => {
		let said = false;
		const fake = await draw((name) => {
			if (name === "orch_reply") {
				said = true;
				return message("A worktree is cheap.", "human:claude");
			}
			return found(said ? { thread: [message("A worktree is cheap.", "human:claude")] } : {});
		});

		const box = screen.getByLabelText("Follow up");
		await act(async () => {
			fireEvent.change(box, { target: { value: "A worktree is cheap." } });
		});
		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Say it" }));
			await settle();
		});

		expect(fake.calls.find((one) => one.name === "orch_reply")?.request).toEqual({
			project: "udl",
			key: "Q-8",
			text: "A worktree is cheap.",
		});
		expect(await screen.findByText("A worktree is cheap.")).toBeTruthy();
	});

	it("closes it with an outcome, and reads it again", async () => {
		let closed = false;
		const fake = await draw((name) => {
			if (name === "orch_close_question") {
				closed = true;
				return {};
			}
			return found(
				closed
					? {
							question: {
								key: "Q-8",
								project: "udl",
								title: "Which colour?",
								text: "",
								asked_by: "deepseek-b",
								to: "humans",
								state: "closed",
								task: null,
								options: [],
								blocking: false,
								answer: null,
								answered_by: null,
								outcome: "Patched it by hand",
								escalated: false,
							},
						}
					: {},
			);
		});

		const box = screen.getByLabelText("Close with outcome");
		await act(async () => {
			fireEvent.change(box, { target: { value: "Patched it by hand" } });
		});
		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Close" }));
			await settle();
		});

		expect(fake.calls.find((one) => one.name === "orch_close_question")?.request).toEqual({
			project: "udl",
			key: "Q-8",
			outcome: "Patched it by hand",
		});
		expect(await screen.findByText("Closed: Patched it by hand")).toBeTruthy();
		// (Closed: there is nothing to answer or follow up.)
		expect(screen.queryByLabelText("Close with outcome")).toBeNull();
	});

	it("answers a question that offers no options, in its own words", async () => {
		const fake = await draw((name) => {
			if (name === "orch_reply" || name === "orch_close_question") {
				throw new Error(`unexpected call ${name}`);
			}
			return found({
				question: {
					key: "Q-8",
					project: "udl",
					title: "Which colour should the badge be?",
					text: "Say what it should be.",
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
			});
		});

		// (No options: the answer box is what there is, and it calls the same `orch_answer`.)
		expect(screen.queryByRole("button", { name: "Keep it" })).toBeNull();
		await act(async () => {
			fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "the blue one" } });
		});
		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Answer" }));
			await settle();
		});

		const called = fake.calls.find((one) => one.name === "orch_answer");
		expect(called?.request).toEqual({ project: "udl", key: "Q-8", answer: "the blue one" });
	});

	it("links the task it was asked in, even when it is the only one named", async () => {
		// (The task a question is asked in is what it is about: its link never depends on the
		// record also listing it among the tasks it names.)
		await draw(() => found({ tasks: [] }));

		expect(screen.getAllByRole("link", { name: "27.3" }).length).toBeGreaterThan(0);
	});

	it("says a follow-up it could not add, and keeps the box", async () => {
		const fake = await draw((name) => {
			if (name === "orch_reply") {
				throw new Error("Q-8 is not yours to reply on");
			}
			return found();
		});

		await act(async () => {
			fireEvent.change(screen.getByLabelText("Follow up"), {
				target: { value: "A worktree is cheap." },
			});
		});
		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Say it" }));
			await settle();
		});

		expect(fake.calls.some((one) => one.name === "orch_reply")).toBe(true);
		expect(screen.getByRole("alert").textContent).toContain("not yours to reply on");
		// (What was typed is still there: a refusal is not a reason to make the reader say it again.)
		expect((screen.getByLabelText("Follow up") as HTMLTextAreaElement).value).toBe(
			"A worktree is cheap.",
		);
	});

	it("says a close it could not make", async () => {
		const fake = await draw((name) => {
			if (name === "orch_close_question") {
				throw new Error("Q-8 is already answered");
			}
			return found();
		});

		await act(async () => {
			fireEvent.change(screen.getByLabelText("Close with outcome"), {
				target: { value: "Patched it by hand" },
			});
		});
		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Close" }));
			await settle();
		});

		expect(fake.calls.some((one) => one.name === "orch_close_question")).toBe(true);
		expect(screen.getByRole("alert").textContent).toContain("already answered");
		expect((screen.getByLabelText("Close with outcome") as HTMLInputElement).value).toBe(
			"Patched it by hand",
		);
	});

	it("takes an empty follow-up or an empty outcome as nothing to say", async () => {
		const fake = await draw(() => found());

		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Say it" }));
			await settle();
		});
		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Close" }));
			await settle();
		});

		// (Neither call went out: an empty box says nothing, and udl would refuse it anyway.)
		expect(fake.calls.map((one) => one.name)).not.toContain("orch_reply");
		expect(fake.calls.map((one) => one.name)).not.toContain("orch_close_question");
	});

	it("links the other tasks and the features it is about", async () => {
		await draw(() =>
			found({
				tasks: ["27.3", "27.4"],
				features: ["27"],
				question: {
					key: "Q-8",
					project: "udl",
					title: "Which one?",
					text: "",
					asked_by: "deepseek-b",
					to: "humans",
					state: "open",
					task: "27.3",
					options: [],
					blocking: false,
					answer: null,
					answered_by: null,
					outcome: null,
					escalated: false,
				},
			}),
		);

		// (The task it names, the other one, and the feature — each a link to its own page.)
		expect(screen.getAllByRole("link", { name: "27.3" }).length).toBeGreaterThan(0);
		expect(screen.getAllByRole("link", { name: "27.4" }).length).toBeGreaterThan(0);
		expect(screen.getAllByRole("link", { name: "27" }).length).toBeGreaterThan(0);
	});

	it("says what reading it failed with, and what a refusal is", async () => {
		render(
			<QuestionPageView
				{...props}
				ctx={
					fakeCtx(() => {
						throw new Error("there is no question called 'Q-8'");
					}).ctx
				}
			/>,
		);
		await act(async () => {
			await settle();
		});

		expect(screen.getByRole("alert").textContent).toContain("there is no question");
	});
});
