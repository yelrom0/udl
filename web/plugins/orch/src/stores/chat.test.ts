/**
 * The chat's store (29.12): one chat at a time, read when the panel opens and again as its
 * harness streams — and remembered in this browser, so a reload resumes it.
 */

import type { JsonObject } from "@udl/ui-sdk";
import { beforeEach, describe, expect, it } from "vitest";
import { fakeCtx, settle } from "../testing/fake";
import { type ChatStore, chatStore } from "./chat";

/** The chat as `orch_chat_get` answers it: one turn said, and its answer. */
function chat(given: JsonObject = {}): JsonObject {
	return {
		id: "c-1",
		owner: "human:claude",
		runner: "claude-code",
		model: "claude-opus-5-5",
		host: "local",
		directory: "/work/udl",
		state: "idle",
		title: "why is the queue stuck?",
		turns: 1,
		last_text: "Hello.",
		permission: null,
		events: [
			{ kind: "said", text: "why is the queue stuck?" },
			{ kind: "text_delta", text: "Hello." },
			{ kind: "turn_ended", reason: "done" },
		],
		total: 3,
		...given,
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

/** The last call `name` was made with (a write is followed by the read that shows its answer). */
function last(calls: { name: string; request: JsonObject }[], name: string) {
	return calls.filter((one) => one.name === name).at(-1);
}

/** A store with `answer` as its udl, made and read once. */
async function storeWith(
	answer: (name: string, request: JsonObject) => JsonObject,
	read = true,
): Promise<{
	store: ChatStore;
	calls: { name: string; request: JsonObject }[];
	fake: ReturnType<typeof fakeCtx>;
}> {
	const fake = fakeCtx(async (name, request) => answer(name, request));
	const made = chatStore(fake.ctx);
	if (read) {
		await made.read();
		await settle();
	}
	return { store: made, calls: fake.calls, fake };
}

beforeEach(() => {
	localStorage.clear();
});

describe("the chat's store", () => {
	it("reads the chats a person has, and draws the one last spoken in", async () => {
		const { store } = await storeWith((name) =>
			name === "orch_chat_list" ? { items: [chat(), chat({ id: "c-2" })] } : chat(),
		);

		expect(store.store.getState().chats.map((one) => one.id)).toEqual(["c-1", "c-2"]);
		expect(store.store.getState().chat?.id).toBe("c-1");
		expect(store.store.getState().loaded).toBe(true);
	});

	it("shows no chat at all when a person asks for a new one", async () => {
		const { store } = await storeWith((name) =>
			name === "orch_chat_list" ? { items: [chat()] } : chat(),
		);

		await store.draft();

		expect(store.store.getState().chat).toBeNull();
		expect(localStorage.getItem("udl.orch.chat")).toBeNull();
	});

	it("does not draw the chat a read was for, once a person asked for a new one", async () => {
		// (The panel's read of the chat on show is in flight when the person asks for a new chat:
		// its answer arrives afterwards, and it must not put the old chat back on show.)
		let answer: () => void = () => {};
		const held = new Promise<void>((done) => {
			answer = done;
		});
		const fake = fakeCtx(async (name) => {
			if (name === "orch_chat_list") {
				return { items: [chat()] } as never;
			}
			await held;
			return chat() as never;
		});
		const made = chatStore(fake.ctx);
		const reading = made.read();
		await settle();
		await made.draft();
		answer();
		await reading;
		await settle();

		expect(made.store.getState().chat).toBeNull();
		expect(made.store.getState().chats.map((one) => one.id)).toEqual(["c-1"]);
	});

	it("reads a chat when it is picked, and keeps it in this browser", async () => {
		const { store, calls } = await storeWith((name) =>
			name === "orch_chat_list" ? { items: [chat()] } : chat(),
		);

		await store.pick("c-1");

		expect(calls.at(-1)).toEqual({ name: "orch_chat_get", request: { chat: "c-1" } });
		expect(store.store.getState().chat?.id).toBe("c-1");
		expect(store.store.getState().chat?.events.map((one) => one.kind)).toEqual([
			"said",
			"text_delta",
			"turn_ended",
		]);
		expect(localStorage.getItem("udl.orch.chat")).toBe("c-1");
	});

	it("resumes the chat this browser last had, when it is opened again", async () => {
		localStorage.setItem("udl.orch.chat", "c-1");
		const { store, calls } = await storeWith((name) =>
			name === "orch_chat_list" ? { items: [chat()] } : chat(),
		);

		expect(store.store.getState().chat?.id).toBe("c-1");
		expect(calls.map((one) => one.name)).toContain("orch_chat_get");
	});

	it("forgets a chat this browser kept that udl no longer has", async () => {
		localStorage.setItem("udl.orch.chat", "c-gone");
		const { store } = await storeWith((name) =>
			name === "orch_chat_list" ? { items: [chat()] } : chat(),
		);

		expect(store.store.getState().chat).toBeNull();
		expect(store.store.getState().error).toBeNull();
		expect(localStorage.getItem("udl.orch.chat")).toBeNull();
	});

	it("says a chat it kept that udl refuses, and forgets it", async () => {
		localStorage.setItem("udl.orch.chat", "c-gone");
		const { store } = await storeWith((name) => {
			if (name === "orch_chat_get") {
				throw new Error("There is no chat c-gone");
			}
			return { items: [chat({ id: "c-gone" })] };
		});

		expect(store.store.getState().chat).toBeNull();
		expect(store.store.getState().error).toBe("There is no chat c-gone");
		expect(localStorage.getItem("udl.orch.chat")).toBeNull();
	});

	it("starts a chat, and shows it", async () => {
		const { store, calls } = await storeWith((name) =>
			name === "orch_chat_list"
				? { items: [chat({ state: "running" })] }
				: chat({ state: "running" }),
		);

		await store.start({
			runner: "claude-code",
			host: "local",
			directory: "/work/udl",
			model: "claude-opus-5-5",
		});

		expect(last(calls, "orch_chat_start")).toEqual({
			name: "orch_chat_start",
			request: {
				runner: "claude-code",
				host: "local",
				directory: "/work/udl",
				model: "claude-opus-5-5",
			},
		});
		expect(store.store.getState().chat?.state).toBe("running");
		expect(store.store.getState().chats.map((one) => one.id)).toEqual(["c-1"]);
	});

	it("says what starting a chat failed with, and keeps drawing the list", async () => {
		const { store } = await storeWith((name) => {
			if (name === "orch_chat_start") {
				throw new Error("the harness is not installed");
			}
			return { items: [chat()] };
		});

		await store.start({ runner: "codex", host: "local", directory: "/tmp", model: "" });

		expect(store.store.getState().error).toBe("the harness is not installed");
		expect(store.store.getState().chats.map((one) => one.id)).toEqual(["c-1"]);
	});

	it("keeps no chat when udl answers with no id", async () => {
		const { store } = await storeWith((name) =>
			name === "orch_chat_start" ? { id: "" } : { items: [chat()] },
		);

		await store.start({ runner: "codex", host: "local", directory: "/tmp", model: "" });

		expect(store.store.getState().chat?.id).toBe("");
		expect(localStorage.getItem("udl.orch.chat")).toBeNull();
	});

	it("says what a write failed with, and draws what it had", async () => {
		const { store } = await storeWith((name) => {
			if (name === "orch_chat_send") {
				throw new Error("the chat has ended");
			}
			return name === "orch_chat_list" ? { items: [chat()] } : chat();
		});
		await store.pick("c-1");

		await store.send("hello?");

		expect(store.store.getState().error).toBe("the chat has ended");
		expect(store.store.getState().chat?.id).toBe("c-1");
	});

	it("says something to the chat, and reads what it answered", async () => {
		const { store, calls } = await storeWith((name, request) => {
			if (name === "orch_chat_send") {
				return chat({ state: "running" });
			}
			if (name === "orch_chat_get") {
				return chat({
					events: [
						{ kind: "said", text: String(request.text ?? "hi") },
						{ kind: "text_delta", text: "On it." },
					],
				});
			}
			return { items: [chat()] };
		});
		await store.pick("c-1");

		await store.send("what is stuck?");

		expect(last(calls, "orch_chat_send")).toEqual({
			name: "orch_chat_send",
			request: { chat: "c-1", text: "what is stuck?", wait: false },
		});
		expect(store.store.getState().chat?.state).toBe("idle");
		expect(store.store.getState().chat?.events.at(-1)?.text).toBe("On it.");
	});

	it("does not say anything when there is no chat", async () => {
		const { store, calls } = await storeWith(() => ({ items: [] }));

		await store.send("hello?");

		expect(calls.map((one) => one.name)).not.toContain("orch_chat_send");
	});

	it("answers what the harness asked, and the card goes once it is answered", async () => {
		let asked: JsonObject | null = ASKED;
		const { store, calls } = await storeWith((name) => {
			if (name === "orch_chat_permission") {
				asked = null;
				return chat({ state: "running" });
			}
			if (name === "orch_chat_get") {
				return chat({ state: "running", permission: asked });
			}
			return { items: [chat()] };
		});
		await store.pick("c-1");
		expect(store.store.getState().chat?.permission?.tool).toBe("Bash");

		await store.answer("p-1", "allow_chat");

		expect(last(calls, "orch_chat_permission")).toEqual({
			name: "orch_chat_permission",
			request: { chat: "c-1", request: "p-1", decision: "allow_chat", message: "" },
		});
		expect(store.store.getState().chat?.permission).toBeNull();
	});

	it("denies with what the person said", async () => {
		const { store, calls } = await storeWith((name) =>
			name === "orch_chat_get" ? chat({ permission: ASKED }) : { items: [chat()] },
		);
		await store.pick("c-1");

		await store.answer("p-1", "deny", "not that folder");

		expect(last(calls, "orch_chat_permission")?.request).toEqual({
			chat: "c-1",
			request: "p-1",
			decision: "deny",
			message: "not that folder",
		});
	});

	it("stops the chat, and forgets the one this browser had", async () => {
		let ended = false;
		const { store, calls } = await storeWith((name) => {
			if (name === "orch_chat_stop") {
				ended = true;
				return chat({ state: "ended" });
			}
			return name === "orch_chat_list"
				? { items: [chat()] }
				: chat({ state: ended ? "ended" : "idle" });
		});
		await store.pick("c-1");

		await store.end();

		expect(last(calls, "orch_chat_stop")).toBeDefined();
		expect(store.store.getState().chat?.state).toBe("ended");
		expect(localStorage.getItem("udl.orch.chat")).toBeNull();
	});

	it("reads the chat again as its harness streams, and once per burst", async () => {
		let reads = 0;
		const { store, fake } = await storeWith((name) => {
			if (name === "orch_chat_get") {
				reads += 1;
				return chat({ state: "running" });
			}
			return { items: [chat()] };
		});
		await store.pick("c-1");
		const before = reads;

		fake.emit("orch.chat.event", { chat: "c-1", kind: "text_delta" });
		fake.emit("orch.chat.event", { chat: "c-1", kind: "text_delta" });
		fake.emit("orch.chat.answered", { chat: "c-1", turn: 1 });
		await settle();

		expect(reads).toBe(before + 1);
	});

	it("leaves another chat's stream alone", async () => {
		let reads = 0;
		const { store, fake } = await storeWith((name) => {
			if (name === "orch_chat_get") {
				reads += 1;
				return chat();
			}
			return { items: [chat()] };
		});
		await store.pick("c-1");
		const before = reads;

		fake.emit("orch.chat.event", { chat: "c-2", kind: "text_delta" });
		await settle();

		expect(reads).toBe(before);
	});

	it("says what a read failed with, and keeps drawing what it had", async () => {
		const { store } = await storeWith((name) => {
			if (name === "orch_chat_get") {
				throw new Error("the network is down");
			}
			return { items: [chat()] };
		});
		await store.pick("c-1");

		expect(store.store.getState().error).toBe("the network is down");
		expect(store.store.getState().chats).toHaveLength(1);
	});

	it("stops listening when it is stopped", async () => {
		const { store, fake } = await storeWith(() => ({ items: [chat()] }));

		store.stop();

		expect(fake.listeners()).toBe(0);
	});
});
