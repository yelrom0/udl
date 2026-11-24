import type { JsonObject } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import type { StoreApi } from "zustand/vanilla";
import type { Question } from "../model";
import { fakeCtx, settle } from "../testing/fake";
import { type QuestionsState, questionsStore } from "./questions";

/** A question as `orch_list_questions` sends one (28.1). */
function question(key: string, over: JsonObject = {}): JsonObject {
	return {
		key,
		project: "udl",
		title: `Question ${key}`,
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
		...over,
	};
}

describe("the questions store (29.7)", () => {
	it("reads every question once when it is made, in every state", async () => {
		const fake = fakeCtx(() => ({ project: "udl", questions: [question("Q-1")] }));

		const { store, stop } = questionsStore(fake.ctx);
		await settle();

		expect(questions(store).map((one) => one.key)).toEqual(["Q-1"]);
		expect(store.getState().error).toBeNull();
		// (Every state: the answered ones are the point of the page, and the operation's default
		// is the open ones alone.)
		expect(fake.calls[0]?.request).toEqual({ state: null });
		expect(store.getState().project).toBe("udl");
		stop();
	});

	it("reads again when a question is asked or answered (28.5's events)", async () => {
		let asked = 0;
		const fake = fakeCtx(() => {
			asked += 1;
			return { project: "udl", questions: [question(`Q-${asked}`)] };
		});

		const { store, stop } = questionsStore(fake.ctx);
		await settle();
		expect(questions(store).map((one) => one.key)).toEqual(["Q-1"]);

		await actEmit(fake, "orch.question.asked");
		expect(questions(store).map((one) => one.key)).toEqual(["Q-2"]);

		await actEmit(fake, "orch.question.answered");
		expect(questions(store).map((one) => one.key)).toEqual(["Q-3"]);

		// (And nothing else moves it: another event is not a question's.)
		await actEmit(fake, "orch.task.ready");
		expect(questions(store).map((one) => one.key)).toEqual(["Q-3"]);
		stop();
	});

	it("says what reading them failed with", async () => {
		const fake = fakeCtx(() => {
			throw new Error("the database is closed");
		});

		const { store, stop } = questionsStore(fake.ctx);
		await settle();

		expect(store.getState().questions).toBeNull();
		expect(store.getState().project).toBe("");
		expect(store.getState().error).toContain("the database is closed");
		stop();
	});

	it("reads nothing after it is stopped", async () => {
		const fake = fakeCtx(() => ({ project: "udl", questions: [question("Q-1")] }));

		const { store, stop } = questionsStore(fake.ctx);
		await settle();
		const before = store.getState().questions;
		const reads = fake.calls.filter((one) => one.name === "orch_list_questions").length;
		stop();

		await actEmit(fake, "orch.question.asked");
		expect(store.getState().questions).toBe(before);
		expect(fake.calls.filter((one) => one.name === "orch_list_questions")).toHaveLength(reads);
	});
});

/** The questions as the store holds them (null until the first read lands). */
function questions(store: StoreApi<QuestionsState>): readonly Question[] {
	const found = store.getState().questions;
	expect(found, "the questions have been read").not.toBeNull();
	return found ?? [];
}

/** Publish an event and let the read it starts settle. */
async function actEmit(fake: ReturnType<typeof fakeCtx>, type: string): Promise<void> {
	fake.emit(type, { project: "udl" });
	await settle();
	await settle();
}
