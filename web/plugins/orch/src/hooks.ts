import type { UIContext } from "@udl/ui-sdk";
import { useCallback, useEffect, useState } from "react";
import { mergeListOf, progressOf, type Session } from "./model";
import { type AgentState, type AgentsState, agentStore, agentsStore } from "./stores/agents";
import { type BoardState, boardStore } from "./stores/board";
import { type FeatureState, featureStore } from "./stores/feature";
import { type InboxState, inboxStore, type Loaded } from "./stores/inbox";
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

/** The harnesses an agent may be given (29.6, 25.11). */
export function useRunners(ctx: UIContext): Refreshable<RunnersState> {
	return useLoaded(ctx, runnersStore, NO_RUNNERS);
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
