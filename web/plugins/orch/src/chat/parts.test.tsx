/**
 * The chat's parts on their own (29.12): the header's picker and its line, the permission card,
 * the new-chat form and the transcript's empty and queued cases.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import type { JsonObject } from "@udl/ui-sdk";
import { describe, expect, it, vi } from "vitest";
import { chatOf, type Runner } from "../model";
import { Chats, Context } from "./header";
import { NewChat } from "./new";
import { Permission } from "./permission";
import { Transcript } from "./transcript";

/** The chat as `orch_chat_get` answers it. */
function chat(over: JsonObject = {}): ReturnType<typeof chatOf> {
	return chatOf({
		id: "c-1",
		owner: "human:claude",
		runner: "claude-code",
		model: "claude-opus-5-5",
		host: "local",
		directory: "/work/udl",
		state: "idle",
		title: "why is the queue stuck?",
		events: [],
		total: 0,
		...over,
	});
}

/** An installed harness, as `orch_list_runners` answers one. */
function runner(over: Partial<Runner> = {}): Runner {
	return {
		host: "local",
		runner: "claude-code",
		installed: true,
		signed_in: true,
		version: "2.0.0",
		models: [{ id: "claude-opus-5-5", name: "Claude Opus 5.5" }],
		...over,
	};
}

describe("the header's line", () => {
	it("says the runner when no model was asked for", () => {
		render(<Context chat={chat({ model: "" })} />);

		expect(screen.getByText("claude-code")).toBeTruthy();
	});
});

describe("the chats a person has", () => {
	it("are nothing to pick from when there are none", () => {
		const { container } = render(
			<Chats
				chat={{
					chats: [],
					chat: null,
					loaded: true,
					error: null,
					refresh: async () => {},
					pick: async () => {},
					draft: async () => {},
					start: async () => {},
					send: async () => {},
					answer: async () => {},
					end: async () => {},
				}}
			/>,
		);

		expect(container.textContent).toBe("");
	});

	it("offer a new one when none is drawn, and none otherwise", () => {
		const pick = vi.fn(async () => {});
		const view = {
			chats: [chat(), chat({ id: "c-2", title: "" })],
			chat: null,
			loaded: true,
			error: null,
			refresh: async () => {},
			pick,
			draft: async () => {},
			start: async () => {},
			send: async () => {},
			answer: async () => {},
			end: async () => {},
		};
		const { rerender } = render(<Chats chat={view} />);

		expect(screen.getByRole("option", { name: "A new chat" })).toBeTruthy();
		// (A chat with no title yet is its harness.)
		expect(screen.getByRole("option", { name: "claude-code" })).toBeTruthy();
		fireEvent.change(screen.getByLabelText("Chats"), { target: { value: "c-2" } });
		expect(pick).toHaveBeenCalledWith("c-2");

		rerender(<Chats chat={{ ...view, chat: chat() }} />);
		expect(screen.queryByRole("option", { name: "A new chat" })).toBeNull();
	});
});

describe("the permission card", () => {
	it("is nothing when nothing is waiting", () => {
		const { container } = render(<Permission chat={chat()} onAnswer={async () => {}} />);

		expect(container.textContent).toBe("");
	});

	it("says what a tool wants in JSON when it is not a command, and can be gone back from", () => {
		const onAnswer = vi.fn(async () => {});
		const asked = chat({
			permission: {
				id: "p-1",
				tool: "Edit",
				input: { file_path: "README.md" },
				state: "pending",
			},
		});
		render(<Permission chat={asked} onAnswer={onAnswer} />);

		expect(screen.getByText(/file_path/)).toBeTruthy();
		fireEvent.click(screen.getByRole("button", { name: "Deny" }));
		fireEvent.click(screen.getByRole("button", { name: "Back" }));

		expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
		expect(onAnswer).not.toHaveBeenCalled();
	});

	it("allows once", async () => {
		const onAnswer = vi.fn(async () => {});
		const asked = chat({
			permission: { id: "p-1", tool: "Bash", input: { command: "ls" }, state: "pending" },
		});
		render(<Permission chat={asked} onAnswer={onAnswer} />);

		fireEvent.click(screen.getByRole("button", { name: "Allow once" }));

		expect(onAnswer).toHaveBeenCalledWith("p-1", "allow_once", "");
	});
});

describe("a new chat", () => {
	it("says no harness is installed, and cannot start", () => {
		render(<NewChat runners={[]} started onStart={async () => {}} />);

		expect(screen.getByRole("option", { name: "No harness is installed" })).toBeTruthy();
		expect(screen.getByRole("option", { name: "local" })).toBeTruthy();
		expect(
			(screen.getByRole("button", { name: "Start the chat" }) as HTMLButtonElement).disabled,
		).toBe(true);
	});

	it("takes the first installed harness, and its models", () => {
		render(
			<NewChat
				runners={[runner({ installed: false, runner: "codex" }), runner()]}
				started
				onStart={async () => {}}
			/>,
		);

		expect((screen.getByLabelText("Harness") as HTMLSelectElement).value).toBe("claude-code");
		expect(screen.getByRole("option", { name: "Claude Opus 5.5" })).toBeTruthy();
	});

	it("starts with the model a person picked", async () => {
		const onStart = vi.fn(async () => {});
		render(<NewChat runners={[runner()]} started onStart={onStart} />);

		fireEvent.change(screen.getByLabelText("Model"), {
			target: { value: "claude-opus-5-5" },
		});
		fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "/work/udl" } });
		fireEvent.click(screen.getByRole("button", { name: "Start the chat" }));

		expect(onStart).toHaveBeenCalledWith({
			runner: "claude-code",
			host: "local",
			directory: "/work/udl",
			model: "claude-opus-5-5",
		});
	});

	it("picks another harness, and forgets the model of the one before", () => {
		render(
			<NewChat
				runners={[
					runner(),
					runner({ runner: "codex", models: [{ id: "gpt-5.5", name: "GPT-5.5" }] }),
				]}
				started
				onStart={async () => {}}
			/>,
		);

		fireEvent.change(screen.getByLabelText("Model"), { target: { value: "claude-opus-5-5" } });
		fireEvent.change(screen.getByLabelText("Harness"), { target: { value: "codex" } });

		expect((screen.getByLabelText("Model") as HTMLSelectElement).value).toBe("");
		expect(screen.getByRole("option", { name: "GPT-5.5" })).toBeTruthy();
	});
});

describe("the transcript", () => {
	it("says nothing has been said yet", () => {
		render(<Transcript chat={chat()} />);

		expect(screen.getByText("Nothing said yet.")).toBeTruthy();
	});

	it("says the last thing said when the page of events has none of it", () => {
		render(<Transcript chat={chat({ last_text: "Hello." })} />);

		expect(screen.getByText("Hello.")).toBeTruthy();
	});

	it("says one thing is waiting its turn, and then more than one", () => {
		const { rerender } = render(<Transcript chat={chat({ pending: 1 })} />);
		expect(screen.getByText("1 more waiting its turn")).toBeTruthy();

		rerender(<Transcript chat={chat({ pending: 2 })} />);
		expect(screen.getByText("2 more waiting their turn")).toBeTruthy();
	});

	it("draws a tool that is still running as one", () => {
		render(
			<Transcript
				chat={chat({
					events: [{ kind: "tool_started", name: "Bash", summary: "pnpm test" }],
				})}
			/>,
		);

		const line = screen.getByText("Bash").closest("details");
		expect(line).not.toBeNull();
		expect(within(line as HTMLElement).getByText("Still running.")).toBeTruthy();
	});

	it("draws a tool that failed as one that failed", () => {
		render(
			<Transcript
				chat={chat({
					events: [
						{ kind: "tool_started", name: "Edit", summary: "README.md" },
						{ kind: "tool_finished", name: "Edit", ok: false },
					],
				})}
			/>,
		);

		expect(screen.getByText("failed")).toBeTruthy();
		expect(screen.getByText("It failed.")).toBeTruthy();
	});
});
