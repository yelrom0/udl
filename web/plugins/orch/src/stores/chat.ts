/**
 * The chat's store (29.12): the person's chats, the one the panel is showing, and what its
 * harness is waiting on. The chat is read when it is picked and again as its harness streams
 * (one read at a time, so a burst of lines is one read), and which chat is open is kept in this
 * browser, so a reload resumes it.
 */

import type { JsonObject, UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asObject, messageOf } from "../json";
import { type Chat, chatOf, chatsOf } from "../model";
import type { Loaded } from "./inbox";
import { coalesced } from "./once";

/** Where this browser keeps the chat it was showing. */
const KEPT = "udl.orch.chat";

/** What the chat's store holds. */
export interface ChatState {
	/** The person's chats, the most recently spoken in first. */
	readonly chats: readonly Chat[];
	/** The chat the panel is showing (null: none yet). */
	readonly chat: Chat | null;
	/** Whether the list has been read (a page drawn before it has shows nothing, not "no chats"). */
	readonly loaded: boolean;
	readonly error: string | null;
}

/** What starts a chat: the person's harness, their folder, their model, and where. */
export interface StartChat {
	readonly runner: string;
	readonly host: string;
	readonly directory: string;
	readonly model: string;
}

/** A chat's store, and what a page does with it. */
export interface ChatStore extends Loaded<ChatState> {
	/** Read the chats, and resume the one this browser had (when it still exists). */
	read(): Promise<void>;
	/** Show `chat`, and remember it here. */
	pick(chat: string): Promise<void>;
	/** Show no chat at all, so a new one can be started (this browser keeps none). */
	draft(): Promise<void>;
	/** Start a chat, show it, and remember it. */
	start(request: StartChat): Promise<void>;
	/** Say `text` to the chat (a turn: the panel reads what it answers as it comes). */
	send(text: string): Promise<void>;
	/** Answer what the chat's harness asked. */
	answer(request: string, decision: Decision, message?: string): Promise<void>;
	/** End the chat (its harness's session ends, and it takes no more turns). */
	end(): Promise<void>;
}

/** What a person decides about what a chat's harness asked (27.11's `ChatDecision`). */
export type Decision = "allow_once" | "allow_chat" | "deny";

/** What this browser kept, when it can be read at all. */
function kept(): string | null {
	try {
		const value = localStorage.getItem(KEPT);
		return value === null || value === "" ? null : value;
	} catch {
		return null;
	}
}

/** Keep `chat` as the one this browser is showing (`null`: forget it). */
function keep(chat: string | null): void {
	try {
		if (chat === null) {
			localStorage.removeItem(KEPT);
		} else {
			localStorage.setItem(KEPT, chat);
		}
	} catch {
		// (Private mode, or a full store: the chat lasts this visit.)
	}
}

/** What moves a chat: its harness's stream, a turn's end, and the chat's own beginning and end. */
const WATCHED: readonly string[] = [
	"orch.chat.event",
	"orch.chat.answered",
	"orch.chat.failed",
	"orch.chat.ended",
	"orch.chat.started",
	"orch.chat.permission",
];

/** The chat named `data.chat` (empty when the event names none). */
function chatOfEvent(data: JsonObject): string {
	const named = data.chat;
	return typeof named === "string" ? named : "";
}

/**
 * The chat's store (29.12): its list, the one on show, and every write a person makes. A chat
 * that is over is still drawn (its transcript is the record of the conversation), and the panel
 * starts a new one from its header.
 */
export function chatStore(ctx: UIContext): ChatStore {
	const store = createStore<ChatState>(() => ({
		chats: [],
		chat: null,
		loaded: false,
		error: null,
	}));
	let stopped = false;
	/** The chat the panel is showing (what the stream's events are about). */
	let showing: string | null = null;
	/** Whether a person asked for a new chat (so the latest one is not drawn instead). */
	let drafting = false;
	/** A read of the list and of the chat on show, one at a time. */
	const reads = coalesced(
		async () => {
			const list = await ctx.call("orch_chat_list", {});
			if (stopped) {
				return;
			}
			const chats = chatsOf(list);
			// (A chat this browser kept, when it is still udl's: a deleted one is forgotten.)
			if (showing === null) {
				const remembered = kept();
				if (remembered !== null) {
					// (A chat udl no longer has is forgotten here: it is not a broken page.)
					if (chats.some((one) => one.id === remembered)) {
						showing = remembered;
					} else {
						keep(null);
					}
				} else if (!drafting && chats.length > 0) {
					// (A panel opened with no chat remembered draws the one last spoken in, so a
					// person finds the conversation they were having.)
					showing = chats[0]?.id ?? null;
				}
			}
			if (showing === null) {
				store.setState({ chats, loaded: true, error: null });
				return;
			}
			// (The chat this read is for: a person may ask for a new one while it is in flight,
			// and then this answer is not what the panel shows.)
			const wanted = showing;
			let chat: Chat | null = null;
			try {
				chat = chatOf(await ctx.call("orch_chat_get", { chat: wanted }));
			} catch (error) {
				// (A chat that is gone is not a broken page: this browser forgets it.)
				if (kept() === wanted) {
					keep(null);
				}
				if (showing === wanted) {
					showing = null;
				}
				if (!stopped) {
					store.setState({ chats, loaded: true, error: messageOf(error) });
				}
				return;
			}
			if (!stopped) {
				// (The list is always this read's; the chat on show is only if it is still the one
				// the person is looking at.)
				store.setState(
					showing === wanted
						? { chats, chat, loaded: true, error: null }
						: { chats, loaded: true, error: null },
				);
			}
		},
		(error) => {
			if (!stopped) {
				store.setState({ loaded: true, error: messageOf(error) });
			}
		},
	);

	/** Write to the chat on show, then read what udl answers. */
	const write = async (name: string, request: JsonObject): Promise<void> => {
		try {
			await ctx.call(name, request);
		} catch (error) {
			if (!stopped) {
				store.setState({ error: messageOf(error) });
			}
			return;
		}
		await reads.ask();
	};

	const stopWatching = WATCHED.map((type) =>
		ctx.events.subscribe(type, (event) => {
			const named = chatOfEvent(asObject(event.data));
			// (A chat's events name it: another chat's stream is not this panel's to read.)
			if (showing === null || (named !== "" && named !== showing)) {
				return;
			}
			if (type === "orch.chat.ended") {
				keep(null);
			}
			void reads.ask();
		}),
	);

	const read = async (): Promise<void> => {
		await reads.ask();
	};

	const pick = async (chat: string): Promise<void> => {
		drafting = false;
		showing = chat;
		keep(chat);
		store.setState({ error: null });
		await reads.ask();
	};

	const draft = async (): Promise<void> => {
		drafting = true;
		showing = null;
		keep(null);
		store.setState({ chat: null, error: null });
	};

	const start = async (request: StartChat): Promise<void> => {
		try {
			const value = await ctx.call("orch_chat_start", {
				runner: request.runner,
				host: request.host,
				directory: request.directory,
				model: request.model,
			});
			const made = chatOf(value);
			drafting = false;
			showing = made.id === "" ? null : made.id;
			if (showing !== null) {
				keep(showing);
			}
			if (!stopped) {
				store.setState((state) => ({
					chat: made,
					chats: state.chats.some((one) => one.id === made.id)
						? state.chats
						: [made, ...state.chats],
					error: null,
				}));
			}
		} catch (error) {
			if (!stopped) {
				store.setState({ error: messageOf(error) });
			}
			return;
		}
		await reads.ask();
	};

	const send = async (text: string): Promise<void> => {
		if (showing === null) {
			return;
		}
		// (The panel reads what the turn says as it streams, so it does not wait for the turn.)
		await write("orch_chat_send", { chat: showing, text, wait: false });
	};

	const answer = async (request: string, decision: Decision, message = ""): Promise<void> => {
		if (showing === null) {
			return;
		}
		await write("orch_chat_permission", {
			chat: showing,
			request,
			decision,
			message,
		});
	};

	const end = async (): Promise<void> => {
		if (showing === null) {
			return;
		}
		await write("orch_chat_stop", { chat: showing });
		keep(null);
	};

	return {
		store,
		read,
		pick,
		draft,
		start,
		send,
		answer,
		end,
		refresh: () => reads.ask(),
		stop: () => {
			stopped = true;
			reads.stop();
			for (const unsubscribe of stopWatching) {
				unsubscribe();
			}
		},
	};
}
