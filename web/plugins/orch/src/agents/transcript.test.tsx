import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Spoken } from "../model";
import { Transcript } from "./transcript";

/** One thing a runner said, as a session holds it. */
function said(over: Partial<Spoken> = {}): Spoken {
	return {
		turn: 1,
		at: "2026-11-24T01:00:00Z",
		kind: "text_delta",
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
		...over,
	};
}

describe("a session's transcript (29.6)", () => {
	it("says nothing has been said when nothing has", () => {
		render(<Transcript said={[]} />);

		expect(screen.getByText("Nothing said yet.")).toBeTruthy();
	});

	it("draws one turn's text as one paragraph, and a new turn's apart from it", () => {
		render(
			<Transcript
				said={[said({ text: "I am " }), said({ text: "on it." }), said({ turn: 2, text: "Done." })]}
			/>,
		);

		expect(screen.getByText("I am on it.")).toBeTruthy();
		expect(screen.getByText("Done.")).toBeTruthy();
	});

	it("draws a tool it used, and what it said the tool was doing", () => {
		render(
			<Transcript
				said={[
					said({ kind: "tool_started", name: "bash", summary: "pytest -q" }),
					said({ kind: "tool_started", name: "read" }),
				]}
			/>,
		);

		expect(screen.getByText("▸ bash — pytest -q")).toBeTruthy();
		expect(screen.getByText("▸ read")).toBeTruthy();
	});

	it("draws how a tool ended, and says when it failed", () => {
		render(
			<Transcript
				said={[
					said({ kind: "tool_finished", name: "bash", ok: true }),
					said({ kind: "tool_finished", name: "read", ok: false }),
				]}
			/>,
		);

		expect(screen.getByText("✓ bash")).toBeTruthy();
		expect(screen.getByText("✗ read")).toBeTruthy();
	});

	it("draws what a turn cost, with the cost when the harness said it", () => {
		render(
			<Transcript
				said={[
					said({ kind: "usage", tokens_in: 1200, tokens_out: 300, cost: 0.42 }),
					said({ kind: "usage", tokens_in: 10, tokens_out: 2 }),
				]}
			/>,
		);

		expect(screen.getByText("1,200 in · 300 out · $0.42")).toBeTruthy();
		expect(screen.getByText("10 in · 2 out")).toBeTruthy();
	});

	it("draws how a turn ended, with its reason and what it said last", () => {
		render(
			<Transcript
				said={[
					said({ kind: "turn_ended", turn: 2, reason: "done", text: "Done." }),
					said({ kind: "turn_ended", turn: 3 }),
				]}
			/>,
		);

		expect(screen.getByText("Turn 2 ended (done): Done.")).toBeTruthy();
		expect(screen.getByText("Turn 3 ended")).toBeTruthy();
	});

	it("says what a failed runner said went wrong, and the runner's own message", () => {
		render(
			<Transcript
				said={[
					said({ kind: "runner_failed", message: "it crashed" }),
					said({ kind: "runner_failed" }),
				]}
			/>,
		);

		expect(screen.getByText("it crashed")).toBeTruthy();
		expect(screen.getByText("It failed.")).toBeTruthy();
	});

	it("says when a limited runner's quota is back, and what it said", () => {
		render(
			<Transcript
				said={[
					said({
						kind: "runner_limited",
						message: "Free tier used up.",
						until: "2026-11-24T00:00:00Z",
					}),
					said({ kind: "runner_limited", message: "Out of credit." }),
				]}
			/>,
		);

		// (The moment is the reader's own clock, so only the words around it are asserted.)
		expect(screen.getByText(/^Out of quota until .+\. Free tier used up\.$/)).toBeTruthy();
		expect(screen.getByText("Out of credit.")).toBeTruthy();
	});

	it("draws a kind it does not know as the line it is", () => {
		render(
			<Transcript
				said={[said({ kind: "session_started", text: "here" }), said({ kind: "other" })]}
			/>,
		);

		expect(screen.getByText("session_started: here")).toBeTruthy();
		expect(screen.getByText("other")).toBeTruthy();
	});
});
