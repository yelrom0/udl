import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { JsonObject } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { QaPage } from "./qa";

/** A question as `orch_list_questions` sends one (28.1). */
function question(over: JsonObject = {}): JsonObject {
	return {
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
		...over,
	};
}

/** What `orch_list_questions` answers with. */
function listed(...questions: JsonObject[]): JsonObject {
	return { project: "udl", questions };
}

/** A route's props, as the shell gives them. */
const props = { path: "questions", params: {}, search: {} };

describe("the Q&A page (29.7)", () => {
	it("says what is being asked, blocking first, and what was answered", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_questions") {
				return listed(
					question({ key: "Q-9", title: "Which key?", blocking: false, task: null, options: [] }),
					question(),
					question({
						key: "Q-7",
						title: "Which colour?",
						state: "answered",
						answer: "The first one",
						answered_by: "human:claude",
						blocking: false,
					}),
				);
			}
			throw new Error(`unexpected call ${name}`);
		});

		await act(async () => {
			render(<QaPage {...props} ctx={fake.ctx} />);
			await settle();
		});

		// (The blocking one is pinned on its own, the rest in the "Being asked" card, and the
		// answered one under them with what was decided.)
		expect(screen.getByRole("region", { name: "Blocking" })).toBeTruthy();
		const asked = screen.getByRole("region", { name: "Being asked" });
		expect(within(asked).getByText("Which key?")).toBeTruthy();
		const answered = screen.getByRole("region", { name: "Answered" });
		expect(within(answered).getByText("Which colour?")).toBeTruthy();
		expect(within(answered).getByText(/human:claude: The first one/)).toBeTruthy();
		// (And the count says what there is, so a page left open is not read as nothing new.)
		expect(screen.getByText("2 open")).toBeTruthy();
	});

	it("answers in one click, and the question leaves after the read", async () => {
		// (The answer is kept, so the read after it sees the question answered: what the page
		// draws is what udl would say next.)
		let answered = false;
		const fake = fakeCtx((name) => {
			if (name === "orch_list_questions") {
				return listed(
					answered
						? question({ state: "answered", answer: "Keep it", answered_by: "human:claude" })
						: question(),
				);
			}
			if (name === "orch_answer") {
				answered = true;
				return question({ state: "answered", answer: "Keep it", answered_by: "human:claude" });
			}
			throw new Error(`unexpected call ${name}`);
		});
		await act(async () => {
			render(<QaPage {...props} ctx={fake.ctx} />);
			await settle();
		});

		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
			await settle();
		});

		const called = fake.calls.find((one) => one.name === "orch_answer");
		expect(called?.request).toEqual({ project: "udl", key: "Q-8", answer: "Keep it" });
		// (The blocking card has gone: the question is answered.)
		expect(screen.queryByRole("region", { name: "Blocking" })).toBeNull();
	});

	it("keeps a refusal and says it, rather than answering", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_questions") {
				return listed(question());
			}
			if (name === "orch_answer") {
				throw new Error("Q-8 is not yours to answer");
			}
			throw new Error(`unexpected call ${name}`);
		});
		await act(async () => {
			render(<QaPage {...props} ctx={fake.ctx} />);
			await settle();
		});

		await act(async () => {
			fireEvent.click(screen.getByRole("button", { name: "Make a new one" }));
			await settle();
		});

		expect(screen.getByRole("alert").textContent).toContain("not yours to answer");
		// (It is still being asked: the refusal is not an answer.)
		expect(screen.getByRole("region", { name: "Blocking" })).toBeTruthy();
	});

	it("reads again when a question is asked or answered (28.5's events)", async () => {
		const fake = fakeCtx(() => listed());
		await act(async () => {
			render(<QaPage {...props} ctx={fake.ctx} />);
			await settle();
		});
		const before = fake.calls.filter((one) => one.name === "orch_list_questions").length;

		await act(async () => {
			fake.emit("orch.question.asked", { project: "udl", question: "Q-9" });
			await settle();
		});

		expect(fake.calls.filter((one) => one.name === "orch_list_questions").length).toBe(before + 1);
	});

	it("says it is reading, until the questions are read", () => {
		// (A read that never lands: the page must not say "0 open" as if nothing were asked.)
		const fake = fakeCtx(() => new Promise(() => {}));

		render(<QaPage {...props} ctx={fake.ctx} />);

		expect(screen.getByText("Reading what is being asked…")).toBeTruthy();
		expect(screen.queryByText("0 open")).toBeNull();
	});

	it("says what reading them failed with", async () => {
		const fake = fakeCtx(() => {
			throw new Error("the database is closed");
		});

		await act(async () => {
			render(<QaPage {...props} ctx={fake.ctx} />);
			await settle();
		});

		expect(screen.getByRole("alert").textContent).toContain("the database is closed");
	});
});
