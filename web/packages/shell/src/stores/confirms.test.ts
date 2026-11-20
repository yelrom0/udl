/**
 * "Are you sure?" as a promise: what a plugin's `ctx.confirm` is. One question is on the page
 * at a time, and the ones asked while it waits are answered in turn.
 */

import { expect, it, vi } from "vitest";
import { answer, ask, confirms } from "./confirms";

it("resolves true when the question is answered yes", async () => {
	confirms.setState({ asked: [] });

	const said = ask("Delete the site?");

	answer(true);

	await expect(said).resolves.toBe(true);
	expect(confirms.getState().asked).toEqual([]);
});

it("resolves false when it is answered no", async () => {
	confirms.setState({ asked: [] });

	const said = ask("Delete the site?", { ok: "Delete", cancel: "Keep it" });

	expect(confirms.getState().asked[0]).toMatchObject({
		question: "Delete the site?",
		ok: "Delete",
		cancel: "Keep it",
	});
	answer(false);

	await expect(said).resolves.toBe(false);
});

it("names the buttons the page's own way when the caller does not", async () => {
	confirms.setState({ asked: [] });

	void ask("Delete the site?");

	expect(confirms.getState().asked[0]).toMatchObject({ ok: "OK", cancel: "Cancel" });
});

it("answers the questions in the order they were asked", async () => {
	confirms.setState({ asked: [] });

	const first = ask("First?");
	const second = ask("Second?");

	expect(confirms.getState().asked.map((one) => one.question)).toEqual(["First?", "Second?"]);
	answer(true);
	answer(false);

	await expect(first).resolves.toBe(true);
	await expect(second).resolves.toBe(false);
});

it("says nothing was asked when an answer comes with no question", () => {
	confirms.setState({ asked: [] });

	answer(true);

	expect(confirms.getState().asked).toEqual([]);
});

it("takes the listener a page uses to draw the question", async () => {
	confirms.setState({ asked: [] });
	const drawn = vi.fn();
	const stop = confirms.subscribe((state) => drawn(state.asked.length));

	void ask("Delete the site?");

	expect(drawn).toHaveBeenCalled();
	stop();
});
