import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { AgentsPage } from "./agents";

/** What udl answers for the tree: a boss, its two reports, and an agent that reports to the
 * humans. */
const ANSWERS: Record<string, unknown> = {
	orch_list_agents: {
		items: [
			{
				agent: { name: "top", title: "The top", role: "manager", manager: null },
				status: "idle",
				reports: ["boss"],
			},
			{
				agent: {
					name: "boss",
					title: "The boss",
					role: "manager",
					manager: "top",
					runner: "opencode",
					model: "deepseek-v4.1",
					host: "local",
					manages: { assign: true, answer: false, review: true, plan: false },
				},
				status: "idle",
				reports: ["deepseek-a", "deepseek-b"],
			},
			{
				agent: {
					name: "deepseek-a",
					title: "DeepSeek A",
					role: "implementer",
					manager: "boss",
					runner: "opencode",
					model: "deepseek-v4.1",
					host: "local",
					projects: ["udl"],
					brief: "You build udl.",
				},
				status: "working",
				session: { id: "s-1", agent: "deepseek-a", task: "29.6", state: "running" },
				queue: [
					{ key: "29.6", project: "udl", feature: "29", title: "The agents", state: "in_progress" },
					{ key: "29.12", project: "udl", feature: "29", title: "The chat", state: "queued" },
				],
			},
			{
				agent: { name: "deepseek-b", title: "DeepSeek B", role: "implementer", manager: "boss" },
				status: "idle",
				queue: [],
			},
			{
				agent: { name: "ernie-c", title: "Ernie C", role: "implementer", manager: null },
				status: "offline",
				queue: [],
			},
		],
	},
	orch_list_runners: {
		items: [
			{
				host: "local",
				runner: "opencode",
				health: { installed: true, signed_in: true, version: "1.2.3" },
				models: [{ id: "deepseek-v4.1", name: "DeepSeek V4.1" }],
			},
			{
				host: "jeff-wiggle",
				runner: "codex",
				health: { installed: true, signed_in: false },
				models: [{ id: "gpt-5", name: "GPT-5" }],
			},
		],
	},
	orch_set_agent: { name: "deepseek-a" },
};

/** The page, drawn, once its read has answered. */
async function read() {
	await settle();
	await screen.findByRole("heading", { level: 1, name: "Agents" });
}

describe("the agents' page (29.6)", () => {
	it("draws the tree from the humans down, each with its status and what is queued", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		expect(screen.getByLabelText("Humans")).toBeTruthy();
		// (The two roots: the boss and the agent that reports to the humans.)
		const top = within(screen.getByLabelText("The humans' reports"));
		expect(top.getByLabelText("top")).toBeTruthy();
		expect(top.getByLabelText("ernie-c")).toBeTruthy();
		// (The boss, under the top, and its own reports under it.)
		expect(within(screen.getByLabelText("top's reports")).getByLabelText("boss")).toBeTruthy();
		const under = within(screen.getByLabelText("boss's reports"));
		expect(under.getByLabelText("deepseek-a")).toBeTruthy();
		expect(under.getByLabelText("deepseek-b")).toBeTruthy();
		// (What each is doing, and how much waits for it.)
		expect(within(screen.getByLabelText("deepseek-a")).getByLabelText("working")).toBeTruthy();
		expect(within(screen.getByLabelText("deepseek-a")).getByText("2 queued")).toBeTruthy();
		expect(within(screen.getByLabelText("ernie-c")).getByLabelText("offline")).toBeTruthy();
		expect(within(screen.getByLabelText("ernie-c")).getByText("nothing queued")).toBeTruthy();
	});

	it("goes to an agent's page", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		expect(
			within(screen.getByLabelText("deepseek-a"))
				.getByRole("link", { name: "deepseek-a" })
				.getAttribute("href"),
		).toBe("/ui/p/orch/agents/deepseek-a");
	});

	it("says who each reports to, in a line", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		expect(screen.getByText(/deepseek-a reports to top › boss/)).toBeTruthy();
		expect(screen.getByText(/boss reports to top/)).toBeTruthy();
		expect(screen.getByText(/top reports to the humans/)).toBeTruthy();
	});

	it("gives an agent to a manager it is dragged onto", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		const from = screen.getByLabelText("ernie-c");
		fireEvent.dragStart(from);
		fireEvent.dragOver(screen.getByLabelText("boss"));
		fireEvent.drop(screen.getByLabelText("boss"));
		await settle();

		expect(
			fake.calls.filter((one) => one.name === "orch_set_agent").map((one) => one.request),
		).toEqual([{ name: "ernie-c", manager: "boss" }]);
		// (It reads the tree again, so the move is drawn.)
		expect(fake.calls.filter((one) => one.name === "orch_list_agents")).toHaveLength(2);
		expect(fake.toasts).toEqual([]);
	});

	it("gives an agent back to the humans when it is dropped on them", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.dragStart(screen.getByLabelText("deepseek-a"));
		fireEvent.drop(screen.getByLabelText("Humans"));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_set_agent")?.request).toEqual({
			name: "deepseek-a",
			manager: "",
		});
	});

	it("refuses a target that is not a manager, and says why", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.dragStart(screen.getByLabelText("ernie-c"));
		fireEvent.drop(screen.getByLabelText("deepseek-a"));
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_set_agent")).toEqual([]);
		expect(fake.toasts).toEqual([
			{ message: "deepseek-a is an implementer: only a manager takes reports.", kind: "error" },
		]);
	});

	it("refuses a drop that would make a loop, and says why", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.dragStart(screen.getByLabelText("top"));
		fireEvent.drop(screen.getByLabelText("boss"));
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_set_agent")).toEqual([]);
		expect(fake.toasts).toEqual([
			{ message: "That would make a loop: top already reports up through boss.", kind: "error" },
		]);
	});

	it("says why when udl refuses the move", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_set_agent") {
				throw new Error("boss is paused");
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.dragStart(screen.getByLabelText("ernie-c"));
		fireEvent.drop(screen.getByLabelText("boss"));
		await settle();

		expect(fake.toasts).toEqual([{ message: "boss is paused", kind: "error" }]);
	});

	it("makes an agent: its name, title, harness, model, role and brief", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "New agent" }));
		// (The harnesses are the editor's own read: the model list is there once it answers.)
		await settle();
		const form = within(screen.getByRole("form", { name: "New agent" }));
		fireEvent.change(form.getByLabelText("Name"), { target: { value: "deepseek-c" } });
		fireEvent.change(form.getByLabelText("Title"), { target: { value: "DeepSeek C" } });
		fireEvent.change(form.getByLabelText("Runner"), { target: { value: "opencode" } });
		fireEvent.change(form.getByLabelText("Model"), { target: { value: "deepseek-v4.1" } });
		fireEvent.change(form.getByLabelText("Brief"), { target: { value: "You build udl." } });
		fireEvent.click(form.getByRole("button", { name: "Save" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_set_agent")?.request).toEqual({
			name: "deepseek-c",
			title: "DeepSeek C",
			runner: "opencode",
			host: "local",
			model: "deepseek-v4.1",
			role: "implementer",
			brief: "You build udl.",
			manages: { assign: false, answer: false, review: false, plan: false },
		});
		// (Saved, it puts the form away and reads the tree again.)
		expect(screen.queryByRole("form", { name: "New agent" })).toBeNull();
	});

	it("offers the models of the harness chosen, and only those", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "New agent" }));
		await settle();
		const form = within(screen.getByRole("form", { name: "New agent" }));
		fireEvent.change(form.getByLabelText("Runner"), { target: { value: "codex" } });
		fireEvent.change(form.getByLabelText("Host"), { target: { value: "jeff-wiggle" } });

		const models = within(form.getByLabelText("Model"));
		expect(models.getByRole("option", { name: "GPT-5" })).toBeTruthy();
		expect(models.queryByRole("option", { name: "DeepSeek V4.1" })).toBeNull();
	});

	it("edits an agent it is given: its name is not a field", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.click(
			within(screen.getByLabelText("deepseek-a")).getByRole("button", { name: "Edit" }),
		);
		const form = within(screen.getByRole("form", { name: "Edit deepseek-a" }));
		expect((form.getByLabelText("Name") as HTMLInputElement).disabled).toBe(true);
		expect((form.getByLabelText("Brief") as HTMLTextAreaElement).value).toBe("You build udl.");
		fireEvent.change(form.getByLabelText("Title"), { target: { value: "DeepSeek A!" } });
		fireEvent.click(form.getByRole("button", { name: "Save" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_set_agent")?.request).toMatchObject({
			name: "deepseek-a",
			title: "DeepSeek A!",
			role: "implementer",
		});
	});

	it("gives a manager its four powers, and no one else any", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "New agent" }));
		const form = within(screen.getByRole("form", { name: "New agent" }));
		expect(form.queryByRole("checkbox", { name: /^Assign/ })).toBeNull();

		fireEvent.change(form.getByLabelText("Role"), { target: { value: "manager" } });
		expect(form.getByRole("checkbox", { name: /^Assign/ })).toBeTruthy();
		fireEvent.click(form.getByRole("checkbox", { name: /^Review/ }));
		fireEvent.change(form.getByLabelText("Name"), { target: { value: "boss2" } });
		fireEvent.click(form.getByRole("button", { name: "Save" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_set_agent")?.request).toMatchObject({
			name: "boss2",
			role: "manager",
			manages: { assign: false, answer: false, review: true, plan: false },
		});
	});

	it("says what went wrong when udl refuses the save", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_set_agent") {
				throw new Error("a runner is needed");
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "New agent" }));
		const form = within(screen.getByRole("form", { name: "New agent" }));
		fireEvent.change(form.getByLabelText("Name"), { target: { value: "nobody" } });
		fireEvent.click(form.getByRole("button", { name: "Save" }));
		await settle();

		expect(fake.toasts).toEqual([{ message: "a runner is needed", kind: "error" }]);
		// (The form stays: what was typed is not thrown away.)
		expect(screen.getByRole("form", { name: "New agent" })).toBeTruthy();
	});

	it("says what went wrong when the agents cannot be read", async () => {
		const fake = fakeCtx(() => {
			throw new Error("it is down");
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await settle();

		expect(screen.getByText("it is down")).toBeTruthy();
	});
});

describe("the agents' page: the drag's edges (29.6)", () => {
	it("carries nothing once the drag ends without a drop", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		const from = screen.getByLabelText("ernie-c");
		fireEvent.dragStart(from);
		fireEvent.dragEnd(from);
		fireEvent.drop(screen.getByLabelText("boss"));
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_set_agent")).toEqual([]);
	});

	it("draws the row a drag is over, and not the one it left", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		const onto = screen.getByLabelText("boss");
		const row = onto.querySelector("div");
		expect(row).not.toBeNull();
		fireEvent.dragStart(screen.getByLabelText("ernie-c"));
		fireEvent.dragOver(onto);
		expect(row?.className).toContain("border-primary");
		fireEvent.dragLeave(onto);
		expect(row?.className).not.toContain("border-primary");
	});

	it("does nothing when a drop names an agent the tree does not have", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		// (A drop on a row whose agent is gone from the tree's answer.)
		fireEvent.dragStart(screen.getByLabelText("ernie-c"));
		fireEvent.drop(screen.getByLabelText("boss"));
		await settle();

		// (It is a manager, so this one is written.)
		expect(fake.calls.filter((one) => one.name === "orch_set_agent")).toHaveLength(1);
	});

	it("says a reviewer is a reviewer, not an implementer", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_agents") {
				return {
					items: [
						{
							agent: { name: "checker", title: "The checker", role: "reviewer", manager: null },
							status: "idle",
						},
						{
							agent: { name: "doer", title: "The doer", role: "implementer", manager: null },
							status: "idle",
						},
					],
				} as never;
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await settle();

		fireEvent.dragStart(screen.getByLabelText("doer"));
		fireEvent.drop(screen.getByLabelText("checker"));
		await settle();

		expect(fake.toasts).toEqual([
			{ message: "checker is a reviewer: only a manager takes reports.", kind: "error" },
		]);
	});

	it("reads the tree again after a drop it refused", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.dragStart(screen.getByLabelText("ernie-c"));
		fireEvent.drop(screen.getByLabelText("deepseek-a"));
		await settle();

		expect(fake.calls.filter((one) => one.name === "orch_list_agents")).toHaveLength(2);
	});
});

describe("the agents' page: two drops in a row (29.6)", () => {
	it("moves an agent to the humans, then to a manager, and writes each", async () => {
		const fake = fakeCtx((name) => ANSWERS[name] as never);
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.dragStart(screen.getByLabelText("deepseek-a"));
		fireEvent.drop(screen.getByLabelText("Humans"));
		await settle();

		fireEvent.dragStart(screen.getByLabelText("deepseek-a"));
		fireEvent.drop(screen.getByLabelText("top"));
		await settle();

		expect(
			fake.calls.filter((one) => one.name === "orch_set_agent").map((one) => one.request),
		).toEqual([
			{ name: "deepseek-a", manager: "" },
			{ name: "deepseek-a", manager: "top" },
		]);
	});
});

describe("the agents' page: a host that cannot say what it has (29.6)", () => {
	it("takes the runner's name as text when no harness answered", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_runners") {
				return { items: [], problems: ["jeff-wiggle: no answer"] } as never;
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		fireEvent.click(screen.getByRole("button", { name: "New agent" }));
		await settle();
		const form = within(screen.getByRole("form", { name: "New agent" }));
		// (A select with nothing in it is a dead end: it is a box to type in, once the read says
		// no host has a harness.)
		const runner = form.getByLabelText("Runner") as HTMLInputElement;
		expect(runner.tagName).toBe("INPUT");
		fireEvent.change(runner, { target: { value: "fake" } });
		fireEvent.change(form.getByLabelText("Name"), { target: { value: "typed" } });
		fireEvent.change(form.getByLabelText("Title"), { target: { value: "Typed" } });
		fireEvent.click(form.getByRole("button", { name: "Save" }));
		await settle();

		expect(fake.calls.find((one) => one.name === "orch_set_agent")?.request).toMatchObject({
			name: "typed",
			runner: "fake",
		});
	});
});

describe("an agent's editor: the harnesses answer after it opens (29.6)", () => {
	it("draws the runner as loading, then a select, so nothing a person typed is swapped", async () => {
		let said: (answer: unknown) => void = () => {};
		const late = new Promise((done) => {
			said = done;
		});
		const fake = fakeCtx((name) => {
			if (name === "orch_list_runners") {
				return late as never;
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await screen.findByRole("heading", { level: 1, name: "Agents" });
		fireEvent.click(screen.getByRole("button", { name: "New agent" }));
		const form = within(screen.getByRole("form", { name: "New agent" }));

		// (Before a host answers, the field is not one a person can type a runner into and lose
		// when the answer replaces it: it says it is reading them, and takes nothing.)
		const reading = form.getByLabelText("Runner") as HTMLInputElement;
		expect(reading.disabled).toBe(true);
		expect(reading.value).toBe("");

		said({
			items: [{ host: "local", runner: "opencode", health: {}, models: [] }],
			problems: [],
		});
		await settle();

		// (Then it is the select a host's harnesses are chosen from.)
		const runner = form.getByLabelText("Runner") as HTMLSelectElement;
		expect(runner.tagName).toBe("SELECT");
		expect(runner.disabled).toBe(false);
		expect([...runner.options].map((one) => one.value)).toContain("opencode");
	});
});

describe("an agent's editor: the harnesses answer after it opens (29.6)", () => {
	it("shows the agent's own runner while the read is still out", async () => {
		let said: (answer: unknown) => void = () => {};
		const late = new Promise((done) => {
			said = done;
		});
		const fake = fakeCtx((name) => {
			if (name === "orch_list_runners") {
				return late as never;
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await screen.findByRole("heading", { level: 1, name: "Agents" });
		fireEvent.click(
			within(screen.getByLabelText("deepseek-a")).getByRole("button", { name: "Edit" }),
		);
		const form = within(screen.getByRole("form", { name: "Edit deepseek-a" }));

		// (The agent runs opencode, and the field says so before the harnesses answer: a disabled
		// box cannot be typed into, so it may as well say what the agent runs on.)
		const reading = form.getByLabelText("Runner") as HTMLInputElement;
		expect(reading.disabled).toBe(true);
		expect(reading.value).toBe("opencode");

		said({ items: [], problems: ["jeff-wiggle: no answer"] });
		await settle();
		expect((form.getByLabelText("Runner") as HTMLInputElement).value).toBe("opencode");
	});
});

describe("the agents' page: an agent whose manager is not there (29.6)", () => {
	it("draws it at the humans rather than not at all", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_agents") {
				return {
					items: [
						{
							agent: { name: "orphan", title: "Orphan", role: "implementer", manager: "gone" },
							status: "idle",
						},
					],
				} as never;
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		expect(
			within(screen.getByLabelText("The humans' reports")).getByLabelText("orphan"),
		).toBeTruthy();
	});
});

describe("the agents' page: nothing yet (29.6)", () => {
	it("says no agent is there, and draws the humans they may report to", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_agents") {
				return { items: [] } as never;
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		expect(screen.getByText("No agent yet: make one above.")).toBeTruthy();
		expect(screen.getByLabelText("Humans")).toBeTruthy();
		expect(screen.queryByText("Reading the agents…")).toBeNull();
	});
});

describe("an agent's editor: the model it has now (29.6)", () => {
	it("shows its own model even when no host lists it", async () => {
		const fake = fakeCtx((name) => {
			if (name === "orch_list_agents") {
				const answer = ANSWERS.orch_list_agents as { items: { agent: Record<string, unknown> }[] };
				return {
					items: answer.items.map((one) =>
						one.agent.name === "deepseek-a"
							? { ...one, agent: { ...one.agent, model: "a-model-gone" } }
							: one,
					),
				} as never;
			}
			return ANSWERS[name] as never;
		});
		render(<AgentsPage ctx={fake.ctx} params={{}} path="agents" search={{}} />);
		await read();

		// (The fixture's agent runs deepseek-v4.1, which the fake host does not offer.)
		fireEvent.click(
			within(screen.getByLabelText("deepseek-a")).getByRole("button", { name: "Edit" }),
		);
		await settle();

		const form = within(screen.getByRole("form", { name: "Edit deepseek-a" }));
		const model = form.getByLabelText("Model") as HTMLSelectElement;
		expect(model.value).toBe("a-model-gone");
		expect(within(model).getByRole("option", { name: "a-model-gone" })).toBeTruthy();
	});
});
