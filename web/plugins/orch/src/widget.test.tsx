import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "./testing/fake";
import { Widget } from "./widget";

const INBOX = {
	project: "udl",
	merge: {
		project: "udl",
		auto_merge: true,
		merger: "claude",
		ready: [{ task: "29.2" }, { task: "29.3" }],
		coming: [],
		held: [],
		done: [],
	},
	questions: [{ key: "Q-8" }],
	actions: [{ key: "A-1" }, { key: "A-2" }, { key: "A-3" }],
	agents: [],
	updated_at: "2026-11-23T00:00:00Z",
};

describe("the dashboard widget", () => {
	it("says it is reading, until the inbox is read", () => {
		const fake = fakeCtx(() => new Promise(() => {}));

		render(<Widget slot="dashboard" plugin={fake.ctx.plugin} ctx={fake.ctx} />);

		expect(screen.getByText("Reading what waits on a person…").textContent).toBe(
			"Reading what waits on a person…",
		);
	});

	it("counts what waits on a person", async () => {
		const fake = fakeCtx(() => INBOX);

		render(<Widget slot="dashboard" plugin={fake.ctx.plugin} ctx={fake.ctx} />);
		await settle();

		expect((await screen.findByText("2 ready to merge")).textContent).toBe("2 ready to merge");
		expect(screen.getByText("1 question").textContent).toBe("1 question");
		expect(screen.getByText("3 to action").textContent).toBe("3 to action");
	});

	it("says several questions as questions", async () => {
		const fake = fakeCtx(() => ({ ...INBOX, questions: [{ key: "Q-8" }, { key: "Q-9" }] }));

		render(<Widget slot="dashboard" plugin={fake.ctx.plugin} ctx={fake.ctx} />);
		await settle();

		expect((await screen.findByText("2 questions")).textContent).toBe("2 questions");
	});

	it("says when it cannot read them", async () => {
		const fake = fakeCtx(() => {
			throw new Error("udl is not answering");
		});

		render(<Widget slot="dashboard" plugin={fake.ctx.plugin} ctx={fake.ctx} />);
		await settle();

		expect((await screen.findByText("udl is not answering")).textContent).toBe(
			"udl is not answering",
		);
	});
});
