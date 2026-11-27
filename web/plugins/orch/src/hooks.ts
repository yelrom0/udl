import type { UIContext } from "@udl/ui-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import { asArray, asObject, asText } from "./json";
import { mergeListOf, progressOf, type Session } from "./model";
import { type AgentState, type AgentsState, agentStore, agentsStore } from "./stores/agents";
import { type BoardState, boardStore } from "./stores/board";
import {
	type ChatState,
	type ChatStore,
	chatStore,
	type Decision,
	type StartChat,
} from "./stores/chat";
import { type FeatureState, featureStore } from "./stores/feature";
import { type InboxState, inboxStore, type Loaded } from "./stores/inbox";
import { type ProgressState, progressStore } from "./stores/progress";
import { type QuestionsState, questionsStore } from "./stores/questions";
import { type RunnersState, runnersStore } from "./stores/runners";
import { type SessionsState, sessionsStore } from "./stores/sessions";
import { type TaskState, taskStore } from "./stores/task";

/** The inbox before it is read: nothing yet, and nothing failed. */
const NO_INBOX: InboxState = { inbox: null, error: null };

/** What a page that can change what it read is given: the state, and a way to read it again. */
export type Refreshable<T> = T & { refresh(): Promise<void> };

/** The questions before they are read. */
const NO_QUESTIONS: QuestionsState = { project: "", questions: null, error: null };

/** The agents' page before it is read. */
const NO_AGENTS: AgentsState = { items: [], loaded: false, error: null };

/** An agent's page before it is read. */
const NO_AGENT: AgentState = {
	info: null,
	agents: [],
	tasks: [],
	merge: mergeListOf(undefined),
	progress: progressOf(undefined),
	loaded: false,
	error: null,
};

/** The harnesses before they are read. */
const NO_RUNNERS: RunnersState = {
	items: [],
	problems: [],
	answered: false,
	loaded: false,
	error: null,
};

/** The board before it is read. */
const NO_BOARD: BoardState = {
	project: "",
	features: [],
	tasks: [],
	agents: [],
	suggestions: [],
	merge: mergeListOf(undefined),
	progress: progressOf(undefined),
	error: null,
};

/**
 * A store made for the page it is drawn in: made on mount, its state followed, and stopped on
 * unmount (so a page that goes away stops listening to the live connection).
 */
function useLoaded<T>(
	ctx: UIContext,
	make: (ctx: UIContext) => Loaded<T>,
	initial: T,
): Refreshable<T> {
	// (The state is the store's, from the first draw: `initial` is only what is drawn before the
	// effect runs, so it is not a dependency.)
	const [state, setState] = useState<T>(initial);
	const [refresh, setRefresh] = useState<() => Promise<void>>(() => async () => {});
	useEffect(() => {
		const { store, stop, refresh: again } = make(ctx);
		setRefresh(() => again);
		const unsubscribe = store.subscribe((next) => {
			setState(next);
		});
		setState(store.getState());
		return () => {
			unsubscribe();
			stop();
		};
	}, [ctx, make]);
	return { ...state, refresh };
}

/** The inbox (28.4), as a page or the widget reads it. */
export function useInbox(ctx: UIContext): Refreshable<InboxState> {
	return useLoaded(ctx, inboxStore, NO_INBOX);
}

/** A feature's page before it is read. */
const NO_FEATURE: FeatureState = {
	info: null,
	points: { points: 0, merged: 0, forecast: null },
	pace: 0,
	pulls: {},
	error: null,
};

/** A task's page before it is read. */
const NO_TASK: TaskState = {
	info: null,
	estimate: null,
	readiness: null,
	session: null,
	agents: [],
	error: null,
};

/** A feature, with its own row of the project's progress (29.5). */
export function useFeature(ctx: UIContext, key: string): Refreshable<FeatureState> {
	const make = useCallback((inner: UIContext) => featureStore(inner, key), [key]);
	return useLoaded(ctx, make, NO_FEATURE);
}

/** A task, with what it takes, whether its pull may merge, and its session (29.5). */
export function useTask(ctx: UIContext, key: string): Refreshable<TaskState> {
	const make = useCallback((inner: UIContext) => taskStore(inner, key), [key]);
	return useLoaded(ctx, make, NO_TASK);
}

/** Every question, as the Q&A page reads them (29.7). */
export function useQuestions(ctx: UIContext): Refreshable<QuestionsState> {
	return useLoaded(ctx, questionsStore, NO_QUESTIONS);
}

/** The board's four lists (29.4). */
export function useBoard(ctx: UIContext): Refreshable<BoardState> {
	return useLoaded(ctx, boardStore, NO_BOARD);
}

/** The progress page before it is read. */
const NO_PROGRESS: ProgressState = {
	projects: [],
	project: "",
	report: null,
	loaded: false,
	error: null,
};

/** A project's progress (29.11): its burn-up, forecast, features and learned hours. */
export function useProgress(
	ctx: UIContext,
	project: string,
	since: string,
): Refreshable<ProgressState> {
	const make = useCallback(
		(one: UIContext) => progressStore(one, project, since),
		[project, since],
	);
	return useLoaded(ctx, make, NO_PROGRESS);
}

/** The harnesses an agent may be given (29.6, 25.11). */
export function useRunners(ctx: UIContext): Refreshable<RunnersState> {
	return useLoaded(ctx, runnersStore, NO_RUNNERS);
}

/** The harnesses on the local host: what a chat may run on (29.12). */
function runnersOnLocal(ctx: UIContext): Loaded<RunnersState> {
	return runnersStore(ctx, "local");
}

/** The harnesses a chat may start on, read for the host it runs on (29.12). */
export function useChatRunners(ctx: UIContext): Refreshable<RunnersState> {
	return useLoaded(ctx, runnersOnLocal, NO_RUNNERS);
}

/** Every agent (29.6). */
export function useAgents(ctx: UIContext): Refreshable<AgentsState> {
	return useLoaded(ctx, agentsStore, NO_AGENTS);
}

/** One agent, its queue and its project's rates (29.6). */
export function useAgent(ctx: UIContext, name: string): Refreshable<AgentState> {
	const make = useCallback((inner: UIContext) => agentStore(inner, name), [name]);
	return useLoaded(ctx, make, NO_AGENT);
}

/** The keys a chat's composer completes, before they are read. */
const NO_KEYS: readonly string[] = [];

/** The `field` of every item `name` answers, or nothing when it could not be asked. */
async function keysOf(
	ctx: UIContext,
	name: string,
	collection: string,
	field: string,
): Promise<string[]> {
	try {
		// (Each list operation answers its own collection: `tasks`, `features`, `questions`.)
		const answer = asObject(await ctx.call(name, {}));
		return asArray(answer[collection])
			.map((item) => asText(asObject(item)[field]))
			.filter((key) => key !== "");
	} catch {
		// (A key list that cannot be read is a completion with fewer keys, not an error.)
		return [];
	}
}

/**
 * The keys a chat's composer completes (29.12): the project's tasks, its features and the
 * questions asked of it. Read once when the panel opens — a chat is about the work, and the keys
 * are what a person names.
 */
export function useKeys(ctx: UIContext): readonly string[] {
	const [keys, setKeys] = useState<readonly string[]>(NO_KEYS);
	useEffect(() => {
		let stopped = false;
		const read = async (): Promise<void> => {
			const found = [
				...(await keysOf(ctx, "orch_list_tasks", "tasks", "key")),
				...(await keysOf(ctx, "orch_list_features", "features", "key")),
				...(await keysOf(ctx, "orch_list_questions", "questions", "key")),
			];
			if (!stopped) {
				setKeys([...new Set(found)]);
			}
		};
		void read();
		return () => {
			stopped = true;
		};
	}, [ctx]);
	return keys;
}

/** The chat before it is read. */
const NO_CHAT: ChatState = { chats: [], chat: null, loaded: false, error: null };

/** What a chat's panel is given: the state, and every write a person makes. */
export type ChatView = ChatState & {
	refresh(): Promise<void>;
	pick(chat: string): Promise<void>;
	draft(): Promise<void>;
	start(request: StartChat): Promise<void>;
	send(text: string): Promise<void>;
	answer(request: string, decision: Decision, message?: string): Promise<void>;
	end(): Promise<void>;
};

/**
 * The chat (29.12), read when the panel opens and again as its harness streams. Its writes are
 * the store's own (a chat's turn outlives the draw that asked for it), so they go through the
 * store the panel was made with, not a call per render.
 */
export function useChat(ctx: UIContext): ChatView {
	const [state, setState] = useState<ChatState>(NO_CHAT);
	const made = useRef<ChatStore | null>(null);
	useEffect(() => {
		const store = chatStore(ctx);
		made.current = store;
		const unsubscribe = store.store.subscribe(setState);
		setState(store.store.getState());
		void store.read();
		return () => {
			unsubscribe();
			store.stop();
			made.current = null;
		};
	}, [ctx]);
	return {
		...state,
		refresh: async () => {
			await made.current?.read();
		},
		pick: async (chat) => {
			await made.current?.pick(chat);
		},
		draft: async () => {
			await made.current?.draft();
		},
		start: async (request) => {
			await made.current?.start(request);
		},
		send: async (text) => {
			await made.current?.send(text);
		},
		answer: async (request, decision, message) => {
			await made.current?.answer(request, decision, message);
		},
		end: async () => {
			await made.current?.end();
		},
	};
}

/** A session before it is read. */
const NO_SESSION: SessionsState = { sessions: {}, error: null };

/** What a page showing one session is given: that session, and the store's state beside it. */
export type SessionView = Refreshable<SessionsState> & { readonly session: Session | null };

/**
 * One session (29.6), read when the page is drawn and again as its runner streams: the shell
 * publishes a line per event, and the store coalesces them into one read at a time.
 */
export function useSession(ctx: UIContext, session: string): SessionView {
	const make = useCallback(
		(inner: UIContext) => {
			const store = sessionsStore(inner);
			// (A page opened before its agent is read has no id yet: nothing to ask for.)
			if (session !== "") {
				void store.read(session);
			}
			return store;
		},
		[session],
	);
	const loaded = useLoaded(ctx, make, NO_SESSION);
	return { ...loaded, session: loaded.sessions[session] ?? null };
}
