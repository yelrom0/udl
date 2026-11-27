/**
 * The chat's panel (29.12): the transcript, what a chat's harness is waiting on, the composer,
 * and the header that starts, picks, pops out and closes it.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { JsonObject, PanelProps } from "@udl/ui-sdk";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fakeCtx } from "../testing/fake";
import { ChatPanel } from "./chat";

/** The chat as `orch_chat_get` answers it, with one turn said and answered. */
function chat(over: JsonObject = {}): JsonObject {
	return {
		id: "c-1",
		owner: "human:claude",
		runner: "claude-code",
		model: "claude-opus-5-5",
		permission_mode: "manual",
		host: "local",
		directory: "/work/udl",
		state: "idle",
		title: "why is the queue stuck?",
		turns: 1,
		tokens_in: 1200,
		tokens_out: 300,
		cost: "0.42",
		last_text: "Hello.",
		pending: 0,
		permission: null,
		events: [
			{ kind: "said", text: "why is the queue stuck?" },
			{ kind: "text_delta", text: "Hello" },
			{ kind: "text_delta", text: " — the queue is fine." },
			{ kind: "turn_ended", reason: "done" },
		],
		total: 4,
		...over,
	};
}

/** A permission waiting on the person, as the chat's answer carries it. */
const ASKED: JsonObject = {
	id: "p-1",
	tool: "Bash",
	input: { command: "git rebase origin/main" },
	state: "pending",
	message: "",
	allowed_for_chat: false,
	asked_at: "2026-11-24T10:04:00Z",
};

/** The harnesses as `orch_list_runners` answers them (25.11). */
const RUNNERS: JsonObject = {
	items: [
		{
			host: "local",
			runner: "claude-code",
			health: { installed: true, signed_in: true, version: "2.0.0" },
			models: [{ id: "claude-opus-5-5", name: "Claude Opus 5.5" }],
		},
		{
			host: "local",
			runner: "codex",
			health: { installed: false, signed_in: false, version: "" },
			models: [],
		},
	],
	problems: [],
};

/** The panel's props: a fake context, and Close and Pop out recorded. */
function panel(answer: (name: string, request: JsonObject) => JsonObject) {
	const fake = fakeCtx(async (name, request) => answer(name, request));
	const close = vi.fn();
	const popOut = vi.fn();
	const props: PanelProps & { ctx: typeof fake.ctx } = {
		open: true,
		close,
		popOut,
		ctx: fake.ctx,
	};
	return { fake, close, popOut, props };
}

/** What udl answers for a chat with nothing else going on. */
function answered(over: JsonObject = {}) {
	return (name: string): JsonObject => {
		if (name === "orch_chat_list") {
			return { items: [chat()] };
		}
		if (name === "orch_list_runners") {
			return RUNNERS;
		}
		if (name.startsWith("orch_list_")) {
			return { items: [] };
		}
		return chat(over);
	};
}

beforeEach(() => {
	localStorage.clear();
});

describe("the chat's panel", () => {
	it("draws what the person said on the right, and the answer under it", async () => {
		const { props } = panel(answered());

		render(<ChatPanel {...props} />);

		const transcript = await screen.findByLabelText("Transcript");
		expect(within(transcript).getByText("why is the queue stuck?")).toBeTruthy();
		expect(within(transcript).getByText(/the queue is fine/)).toBeTruthy();
	});

	it("says the model, where it runs, its folder and whose it is", async () => {
		const { props } = panel(answered());

		render(<ChatPanel {...props} />);

		const line = await screen.findByText("claude-opus-5-5");
		expect(line.parentElement?.textContent).toContain("local");
		expect(line.parentElement?.textContent).toContain("/work/udl");
		expect(line.parentElement?.textContent).toContain("as human:claude");
	});

	it("draws a tool as one line that opens", async () => {
		const { props } = panel(
			answered({
				events: [
					{ kind: "said", text: "check it" },
					{ kind: "tool_started", name: "Bash", summary: "git status" },
					{ kind: "tool_finished", name: "Bash", ok: true },
					{ kind: "text_delta", text: "Clean." },
				],
			}),
		);

		render(<ChatPanel {...props} />);

		const line = await screen.findByText("Bash");
		expect(line.closest("details")).not.toBeNull();
		expect(screen.getByText("It worked.")).toBeTruthy();
	});

	it("sends with Enter, and a new line with Shift+Enter", async () => {
		const { props, fake } = panel(answered());
		render(<ChatPanel {...props} />);
		const box = await screen.findByLabelText("Message");

		fireEvent.change(box, { target: { value: "hello" } });
		fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
		expect(fake.calls.some((one) => one.name === "orch_chat_send")).toBe(false);

		fireEvent.keyDown(box, { key: "Enter" });
		await waitFor(() => {
			expect(fake.calls.some((one) => one.name === "orch_chat_send")).toBe(true);
		});
		expect(fake.calls.find((one) => one.name === "orch_chat_send")?.request).toEqual({
			chat: "c-1",
			text: "hello",
			wait: false,
		});
	});

	it("does not send an empty message", async () => {
		const { props, fake } = panel(answered());
		render(<ChatPanel {...props} />);

		fireEvent.click(await screen.findByRole("button", { name: "Send" }));

		expect(fake.calls.some((one) => one.name === "orch_chat_send")).toBe(false);
	});

	it("says what is said during a turn is queued for the next one", async () => {
		// (The backend queues it: a chat takes one turn at a time, and the person's next words are
		// the next turn — so the composer sends while one runs, rather than refusing.)
		const { props, fake } = panel(answered({ state: "running" }));
		render(<ChatPanel {...props} />);

		const box = await screen.findByLabelText("Message");
		fireEvent.change(box, { target: { value: "and now the tests" } });
		fireEvent.keyDown(box, { key: "Enter" });

		await waitFor(() => {
			expect(fake.calls.find((one) => one.name === "orch_chat_send")?.request).toEqual({
				chat: "c-1",
				text: "and now the tests",
				wait: false,
			});
		});
	});

	it("ends the chat only when the person says so", async () => {
		const { props, fake } = panel(answered({ state: "running" }));
		render(<ChatPanel {...props} />);

		fake.yes = false;
		fireEvent.click(await screen.findByRole("button", { name: "End chat" }));

		await waitFor(() => expect(fake.asked.length).toBe(1));
		expect(fake.asked[0]).toContain("End this chat?");
		expect(fake.calls.some((one) => one.name === "orch_chat_stop")).toBe(false);

		fake.yes = true;
		fireEvent.click(screen.getByRole("button", { name: "End chat" }));

		await waitFor(() => {
			expect(fake.calls.some((one) => one.name === "orch_chat_stop")).toBe(true);
		});
	});

	it("draws what the harness asked, and allows it for this chat", async () => {
		const { props, fake } = panel(answered({ state: "running", permission: ASKED }));
		render(<ChatPanel {...props} />);

		const card = await screen.findByLabelText("Waiting on you");
		expect(within(card).getByText(/git rebase origin\/main/)).toBeTruthy();

		fireEvent.click(within(card).getByRole("button", { name: "Allow for this chat" }));

		await waitFor(() => {
			expect(fake.calls.find((one) => one.name === "orch_chat_permission")?.request).toEqual({
				chat: "c-1",
				request: "p-1",
				decision: "allow_chat",
				message: "",
			});
		});
	});

	it("denies with why", async () => {
		const { props, fake } = panel(answered({ state: "running", permission: ASKED }));
		render(<ChatPanel {...props} />);

		fireEvent.click(await screen.findByRole("button", { name: "Deny" }));
		fireEvent.change(screen.getByLabelText("Why not?"), {
			target: { value: "not that folder" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Deny it" }));

		await waitFor(() => {
			expect(fake.calls.find((one) => one.name === "orch_chat_permission")?.request).toEqual({
				chat: "c-1",
				request: "p-1",
				decision: "deny",
				message: "not that folder",
			});
		});
	});

	it("closes the panel, and pops it out", async () => {
		const { props, close, popOut } = panel(answered());
		render(<ChatPanel {...props} />);

		fireEvent.click(await screen.findByRole("button", { name: "Close the chat" }));
		fireEvent.click(screen.getByRole("button", { name: "Pop the chat out" }));

		expect(close).toHaveBeenCalledOnce();
		expect(popOut).toHaveBeenCalledOnce();
	});

	it("asks for a harness and a folder when there is no chat yet", async () => {
		const { props, fake } = panel((name) => {
			if (name === "orch_chat_list") {
				return { items: [] };
			}
			if (name === "orch_list_runners") {
				return RUNNERS;
			}
			if (name === "orch_chat_start") {
				return chat({ state: "running" });
			}
			return { items: [] };
		});
		render(<ChatPanel {...props} />);

		const form = await screen.findByLabelText("A new chat");
		await waitFor(() => {
			expect(within(form).getByLabelText("Harness")).toBeTruthy();
		});
		fireEvent.change(within(form).getByLabelText("Folder"), {
			target: { value: "/work/udl" },
		});
		const start = within(form).getByRole("button", { name: "Start the chat" }) as HTMLButtonElement;
		await waitFor(() => {
			expect(start.disabled).toBe(false);
		});
		fireEvent.click(start);

		await waitFor(() => {
			expect(fake.calls.find((one) => one.name === "orch_chat_start")?.request).toEqual({
				runner: "claude-code",
				host: "local",
				directory: "/work/udl",
				model: "",
			});
		});
	});

	it("picks a past chat from the header", async () => {
		const { props, fake } = panel((name) => {
			if (name === "orch_chat_list") {
				return { items: [chat(), chat({ id: "c-2", title: "the second chat" })] };
			}
			if (name === "orch_list_runners") {
				return RUNNERS;
			}
			if (name.startsWith("orch_list_")) {
				return { items: [] };
			}
			return chat({ id: "c-2", title: "the second chat" });
		});
		render(<ChatPanel {...props} />);

		const picker = await screen.findByLabelText("Chats");
		fireEvent.change(picker, { target: { value: "c-2" } });

		await waitFor(() => {
			expect(fake.calls.filter((one) => one.name === "orch_chat_get").at(-1)?.request).toEqual({
				chat: "c-2",
			});
		});
		await waitFor(() => {
			expect(screen.getByText("the second chat")).toBeTruthy();
		});
	});

	it("completes a task's key after an @", async () => {
		const { props, fake } = panel((name) => {
			if (name === "orch_chat_list") {
				return { items: [chat()] };
			}
			if (name === "orch_list_runners") {
				return RUNNERS;
			}
			// (The operations answer their own collection, as udl does: `tasks`, not `items`.)
			if (name === "orch_list_tasks") {
				return { project: "udl", tasks: [{ key: "29.1" }, { key: "29.10" }] };
			}
			if (name === "orch_list_features") {
				return { project: "udl", features: [{ key: "29" }] };
			}
			if (name === "orch_list_questions") {
				return { project: "udl", questions: [] };
			}
			if (name.startsWith("orch_list_")) {
				return { items: [] };
			}
			return chat();
		});
		render(<ChatPanel {...props} />);

		const box = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
		fireEvent.change(box, { target: { value: "look at @29.1", selectionStart: 14 } });

		const list = await screen.findByLabelText("Completions");
		fireEvent.click(within(list).getByRole("button", { name: "29.1" }));

		expect(box.value).toBe("look at @29.1 ");
		expect(fake.calls.some((one) => one.name === "orch_chat_send")).toBe(false);
	});

	it("says the chat is over, and takes no more turns", async () => {
		const { props } = panel(answered({ state: "ended" }));
		render(<ChatPanel {...props} />);

		const box = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;

		expect(box.disabled).toBe(true);
		expect(box.placeholder).toBe("This chat is over.");
	});

	it("says how many things are waiting their turn", async () => {
		const { props } = panel(answered({ pending: 2 }));
		render(<ChatPanel {...props} />);

		expect(await screen.findByText("2 more waiting their turn")).toBeTruthy();
		expect(screen.getByRole("button", { name: "Send (2 waiting)" })).toBeTruthy();
	});

	it("says what a read failed with", async () => {
		const { props } = panel(() => {
			throw new Error("the network is down");
		});
		render(<ChatPanel {...props} />);

		expect(await screen.findByText("the network is down")).toBeTruthy();
	});
});
