/**
 * The transcript's blocks (29.12): a person's words are one block, a run of the harness's text is
 * one paragraph, a tool is one line with how it ended, and a failure or a limit is a note.
 */

import { describe, expect, it } from "vitest";
import type { Spoken } from "../model";
import { blocksOf } from "./blocks";

/** One thing the harness said, as the transcript holds it. */
function said(given: Partial<Spoken>): Spoken {
	return {
		turn: 0,
		at: "",
		kind: "",
		text: "",
		name: "",
		summary: "",
		ok: null,
		cost: null,
		tokens_in: 0,
		tokens_out: 0,
		reason: "",
		message: "",
		until: null,
		...given,
	};
}

describe("a chat's transcript", () => {
	it("is the person's words, then the answer as one paragraph", () => {
		const blocks = blocksOf([
			said({ kind: "said", text: "why is the queue stuck?" }),
			said({ kind: "text_delta", text: "Hello" }),
			said({ kind: "text_delta", text: " — the queue is fine." }),
			said({ kind: "turn_ended", reason: "done" }),
		]);

		expect(blocks).toEqual([
			{ kind: "said", text: "why is the queue stuck?" },
			{ kind: "text", text: "Hello — the queue is fine." },
		]);
	});

	it("is one line per tool call, with whether it worked", () => {
		const blocks = blocksOf([
			said({ kind: "tool_started", name: "Bash", summary: "git status" }),
			said({ kind: "tool_finished", name: "Bash", ok: true }),
			said({ kind: "tool_started", name: "Edit", summary: "README.md" }),
			said({ kind: "tool_finished", name: "Edit", ok: false }),
		]);

		expect(blocks).toEqual([
			{ kind: "tool", name: "Bash", summary: "git status", ok: true },
			{ kind: "tool", name: "Edit", summary: "README.md", ok: false },
		]);
	});

	it("keeps a tool that has not finished as one that is running", () => {
		const blocks = blocksOf([said({ kind: "tool_started", name: "Bash", summary: "pnpm test" })]);

		expect(blocks).toEqual([{ kind: "tool", name: "Bash", summary: "pnpm test", ok: null }]);
	});

	it("says what a failure or a limit was", () => {
		const blocks = blocksOf([
			said({ kind: "runner_failed", message: "it could not start" }),
			said({ kind: "runner_limited", until: "2026-11-25T00:00:00Z" }),
		]);

		expect(blocks).toEqual([
			{ kind: "note", text: "it could not start" },
			{ kind: "note", text: "It is out of quota until 2026-11-25T00:00:00Z." },
		]);
	});

	it("says nothing for a turn that ended because it was done", () => {
		expect(blocksOf([said({ kind: "turn_ended", reason: "done", text: "Hello." })])).toEqual([]);
	});

	it("says why a turn ended when it was not done", () => {
		const blocks = blocksOf([
			said({ kind: "turn_ended", reason: "error", text: "the model fell over" }),
		]);

		expect(blocks).toEqual([{ kind: "note", text: "the model fell over" }]);
	});

	it("keeps the person's words between two answers apart", () => {
		const blocks = blocksOf([
			said({ kind: "said", text: "one" }),
			said({ kind: "text_delta", text: "first" }),
			said({ kind: "said", text: "two" }),
			said({ kind: "text_delta", text: "second" }),
		]);

		expect(blocks).toEqual([
			{ kind: "said", text: "one" },
			{ kind: "text", text: "first" },
			{ kind: "said", text: "two" },
			{ kind: "text", text: "second" },
		]);
	});

	it("does not put what a turn cost in the conversation", () => {
		expect(blocksOf([said({ kind: "usage", tokens_in: 10, tokens_out: 2, cost: 0.01 })])).toEqual(
			[],
		);
	});
});
