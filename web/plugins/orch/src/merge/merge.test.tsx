/**
 * ------------------
 * - merge.test.tsx -
 * ------------------
 *
 * Ready to merge, and To action (29.8): the merge list as the inbox sends it, the project's auto
 * merge said, and what only a person can do. The merge is udl's — the page asks which pull it is,
 * then calls `orch_merge` with the head the row was listed at; a refusal is said, not swallowed.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { JsonObject } from "@udl/ui-sdk";
import { describe, expect, it } from "vitest";
import type { MergeList, MergeRow } from "../model";
import { fakeCtx } from "../testing/fake";
import { Actions } from "./actions";
import { Merge } from "./merge";

/** A merge list, as `orch_inbox` sends one. */
function list(extra: Partial<MergeList> = {}): MergeList {
	return {
		project: "udl",
		auto_merge: false,
		merger: null,
		ready: [],
		coming: [],
		held: [],
		done: [],
		...extra,
	};
}

/** A row of the list. */
function row(number: number, extra: Partial<MergeRow> = {}): MergeRow {
	return {
		project: "udl",
		task: `29.${number}`,
		feature: "29",
		title: `Task ${number}`,
		number,
		repo: "yelrom0/udl",
		url: `https://github.com/yelrom0/udl/pull/${number}`,
		sha: "0123456789abcdef",
		checks: "pass",
		review: "approve",
		merged_by: "",
		state: "ready",
		blockers: [],
		followed: [],
		merger: null,
		...extra,
	};
}

/** The fake context, and the inbox it answers with. */
function draw() {
	return fakeCtx(() => ({ inbox: INBOX }) as JsonObject);
}

/** A page's own `refresh`: what the store gives it, which here is the fake's call again. */
function again(fake: ReturnType<typeof draw>): () => Promise<void> {
	return async () => {
		await fake.ctx.call("orch_inbox", {});
	};
}

describe("Ready to merge (29.8)", () => {
	it("says what is ready, numbered, and the auto merge's state", () => {
		const fake = draw();

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({ ready: [row(3), row(4)] })}
				project="udl"
				refresh={again(fake)}
			/>,
		);

		expect(screen.getByText("Ready now")).toBeTruthy();
		expect(screen.getByText("#3")).toBeTruthy();
		expect(screen.getByText("#4")).toBeTruthy();
		expect(screen.getByText("auto merge: off")).toBeTruthy();
	});

	it("turns auto merge on and off, and says a refusal", async () => {
		const fake = draw();

		const view = (extra: Partial<MergeList>) =>
			render(<Merge ctx={fake.ctx} merge={list(extra)} project="udl" refresh={again(fake)} />);

		// (Off, with a merger stored: the button turns it on, with the project.)
		view({ auto_merge: false, merger: "claude" });
		fireEvent.click(screen.getByRole("button", { name: "Turn on" }));
		await waitFor(() => {
			expect(
				fake.calls.some(
					(one) =>
						one.name === "orch_set_auto_merge" &&
						JSON.stringify(one.request) === JSON.stringify({ project: "udl", on: true }),
				),
			).toBe(true);
		});

		// (On: it turns it off, and the stored merger is kept.)
		view({ auto_merge: true, merger: "claude" });
		fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
		await waitFor(() => {
			expect(
				fake.calls.some(
					(one) =>
						one.name === "orch_set_auto_merge" &&
						JSON.stringify(one.request) === JSON.stringify({ project: "udl", on: false }),
				),
			).toBe(true);
		});
	});

	it("offers no switch to turn on without a merger", () => {
		// (Turning it on needs a merger: with none stored the button is disabled, not a trap.)
		const fake = draw();
		render(
			<Merge
				ctx={fake.ctx}
				merge={list({ auto_merge: false, merger: null })}
				project="udl"
				refresh={again(fake)}
			/>,
		);
		expect((screen.getByRole("button", { name: "Turn on" }) as HTMLButtonElement).disabled).toBe(
			true,
		);
	});

	it("says what a refused switch was refused with", async () => {
		const refused = fakeCtx((name) => {
			if (name === "orch_set_auto_merge") {
				throw new Error("only a person turns auto merge on or off");
			}
			return { inbox: INBOX } as JsonObject;
		});
		render(
			<Merge
				ctx={refused.ctx}
				merge={list({ auto_merge: false, merger: "claude" })}
				project="udl"
				refresh={again(refused)}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: "Turn on" }));
		await waitFor(() => {
			const said = screen.getAllByRole("alert").map((one) => one.textContent ?? "");
			expect(said.some((one) => one.includes("only a person"))).toBe(true);
		});
	});

	it("draws the checks, the review, the merger and who merged it", () => {
		const fake = draw();

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({
					ready: [row(3, { merger: "reviewing", checks: "pass", review: "approve" })],
					done: [
						row(2, {
							state: "done",
							merged_by: "claude (auto merge)",
							followed: ["29.4"],
						}),
					],
				})}
				project="udl"
				refresh={again(fake)}
			/>,
		);

		// (Two rows carry the same words: the badge and the row's own line, so all of them.)
		expect(screen.getAllByText("checks: pass").length).toBeGreaterThan(0);
		expect(screen.getAllByText("review: approve").length).toBeGreaterThan(0);
		expect(screen.getByText("reviewing")).toBeTruthy();
		expect(screen.getByText("merged by claude (auto merge)")).toBeTruthy();
		expect(screen.getByText("followed by 29.4")).toBeTruthy();
		// (Q-8: the title is a link, and an icon says it goes to GitHub.)
		expect(
			document.querySelector('a[href="https://github.com/yelrom0/udl/pull/3"] svg'),
		).toBeTruthy();
	});

	it("says whose merger does auto merge when it is on", () => {
		const fake = draw();

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({ auto_merge: true, merger: "claude" })}
				project="udl"
				refresh={again(fake)}
			/>,
		);

		expect(screen.getByText("auto merge: claude")).toBeTruthy();
	});

	it("merges the pull it is asked about, with the head it was listed at", async () => {
		const fake = draw();
		fake.yes = true;

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({ ready: [row(3)] })}
				project="udl"
				refresh={again(fake)}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: "Merge" }));

		await waitFor(() => {
			expect(fake.calls.some((one) => one.name === "orch_merge")).toBe(true);
		});
		const called = fake.calls.find((one) => one.name === "orch_merge");
		expect(called?.request).toEqual({
			project: "udl",
			key: "29.3",
			confirm: "yelrom0/udl#3",
			sha: "0123456789abcdef",
		});
	});

	it("says what the merge was refused with, rather than losing the reason", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_merge") {
				throw new Error("29.3 has moved since it was listed");
			}
			return { inbox: INBOX } as JsonObject;
		});

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({ ready: [row(3)] })}
				project="udl"
				refresh={again(fake)}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: "Merge" }));

		await waitFor(() => {
			expect(screen.queryByRole("alert")?.textContent).toContain("has moved since it was listed");
		});
	});

	it("takes the confirm with it, so the question names the pull", () => {
		const fake = draw();
		fake.yes = true;

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({ ready: [row(3)] })}
				project="udl"
				refresh={again(fake)}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: "Merge" }));

		expect(fake.asked[0]).toContain("yelrom0/udl#3");
	});

	it("offers Merge only on the ready rows, and none while one is in flight", async () => {
		// (A merge that does not land until the test lets it: every button says so meanwhile.)
		const fake = fakeCtx(() => ({ inbox: INBOX }) as JsonObject);
		const asked = new Promise<void>(() => {});
		const original = fake.ctx.call;
		fake.ctx = {
			...fake.ctx,
			call: async (name, request) => {
				if (name === "orch_merge") {
					await asked;
				}
				return await original(name, request);
			},
		};

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({
					ready: [row(3), row(4)],
					coming: [row(5, { blockers: ["29.6 not merged yet"] })],
					held: [row(6, { blockers: ["the bot says wait"] })],
					done: [row(2, { state: "merged", merged_by: "claude" })],
				})}
				project="udl"
				refresh={again(fake)}
			/>,
		);

		// (Only the ready rows have a button at all.)
		expect(screen.getAllByRole("button", { name: "Merge" })).toHaveLength(2);
		fireEvent.click(screen.getAllByRole("button", { name: "Merge" })[0] as HTMLElement);

		// (While one is in flight every button is disabled, not only the one clicked.)
		await waitFor(() => {
			const buttons = screen.getAllByRole("button", { name: "Merging…" }) as HTMLButtonElement[];
			expect(buttons).toHaveLength(2);
			for (const button of buttons) {
				expect(button.disabled).toBe(true);
			}
		});
		expect(screen.queryAllByRole("button", { name: "Merge" })).toHaveLength(0);
	});

	it("says the merge is going on, and only one at a time", async () => {
		// (A merge that does not land until the test lets it: the button says so meanwhile.)
		const fake = fakeCtx(() => ({ inbox: INBOX }) as JsonObject);
		const asked = new Promise<void>(() => {});
		const original = fake.ctx.call;
		fake.ctx = {
			...fake.ctx,
			call: async (name, request) => {
				if (name === "orch_merge") {
					await asked;
				}
				return await original(name, request);
			},
		};

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({ ready: [row(3)] })}
				project="udl"
				refresh={again(fake)}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: "Merge" }));

		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Merging…" })).toBeTruthy();
		});
	});

	it("does not merge when the person says no", () => {
		const fake = draw();
		fake.yes = false;

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({ ready: [row(3)] })}
				project="udl"
				refresh={again(fake)}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: "Merge" }));

		expect(fake.calls.some((one) => one.name === "orch_merge")).toBe(false);
	});

	it("lists what is coming, held, and merged lately under their own headings", () => {
		const fake = draw();

		render(
			<Merge
				ctx={fake.ctx}
				merge={list({
					coming: [row(5, { blockers: ["29.6 not merged yet"] })],
					held: [row(6, { blockers: ["the bot says wait"] })],
					done: [row(2, { state: "merged" })],
				})}
				project="udl"
				refresh={again(fake)}
			/>,
		);

		expect(screen.getByText("Coming soon")).toBeTruthy();
		expect(screen.getByText("29.6 not merged yet")).toBeTruthy();
		expect(screen.getByText("Held")).toBeTruthy();
		expect(screen.getByText("the bot says wait")).toBeTruthy();
		expect(screen.getByText("Merged lately")).toBeTruthy();
	});
});

describe("To action (29.8)", () => {
	it("groups what only a person can do into now, after and later", () => {
		const fake = draw();

		render(<Actions actions={ACTIONS} ctx={fake.ctx} project="udl" refresh={again(fake)} />);

		expect(screen.getByText("Now")).toBeTruthy();
		expect(screen.getByText("After")).toBeTruthy();
		expect(screen.getByText("Later")).toBeTruthy();
	});

	it("finishes one with its result", async () => {
		const fake = draw();

		render(<Actions actions={[ACTION]} ctx={fake.ctx} project="udl" refresh={again(fake)} />);
		fireEvent.change(screen.getByRole("textbox"), { target: { value: "Reviewed it by hand" } });
		fireEvent.click(screen.getByRole("button", { name: "Done" }));

		await waitFor(() => {
			expect(fake.calls.some((one) => one.name === "orch_finish_action")).toBe(true);
		});
		const called = fake.calls.find((one) => one.name === "orch_finish_action");
		expect(called?.request).toEqual({
			project: "udl",
			key: "A-1",
			result: "Reviewed it by hand",
		});
	});

	it("takes nothing when the result box is empty", () => {
		const fake = draw();

		render(<Actions actions={[ACTION]} ctx={fake.ctx} project="udl" refresh={again(fake)} />);

		const done = screen.getByRole("button", { name: "Done" }) as HTMLButtonElement;
		expect(done.disabled).toBe(true);
	});

	it("says what finishing one was refused with", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_finish_action") {
				throw new Error("A-1 is not a person's to finish");
			}
			return {} as JsonObject;
		});

		render(<Actions actions={[ACTION]} ctx={fake.ctx} project="udl" refresh={again(fake)} />);
		fireEvent.change(screen.getByRole("textbox"), { target: { value: "Reviewed it by hand" } });
		fireEvent.click(screen.getByRole("button", { name: "Done" }));

		await waitFor(() => {
			expect(screen.queryByRole("alert")?.textContent).toContain("not a person's to finish");
		});
	});
});

/** The inbox the page reads, as `orch_inbox` sends it. */
const INBOX = {
	project: "udl",
	merge: {
		project: "udl",
		auto_merge: false,
		merger: null,
		ready: [],
		coming: [],
		held: [],
		done: [],
	},
	questions: [],
	actions: [],
	agents: [],
	updated_at: "2026-11-21T10:00:00Z",
};

/** One thing only a person can do. */
const ACTION = {
	key: "A-1",
	title: "Look at the pull",
	text: "It needs a person",
	when: "now",
	after: null,
	task: "29.3",
	assignee: "human",
};

/** Three of them, one per group. */
const ACTIONS = [
	ACTION,
	{ ...ACTION, key: "A-2", title: "When the base merges", when: "after", after: "29.2" },
	{ ...ACTION, key: "A-3", title: "Later on", when: "later", after: null },
];
