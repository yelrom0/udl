import type { JsonValue, UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asArray, asObject, asText, messageOf } from "../json";
import {
	type Estimate,
	estimateOf,
	type Readiness,
	readinessOf,
	sessionOfTask,
	type TaskInfo,
	type TaskSession,
	taskInfoOf,
} from "../model";
import type { Loaded } from "./inbox";
import { coalesced, on, orNone } from "./once";

/** What a task's page holds: the task, what it takes, whether its pull may merge, and its
 * session. */
export interface TaskState {
	readonly info: TaskInfo | null;
	readonly estimate: Estimate | null;
	readonly readiness: Readiness | null;
	readonly session: TaskSession | null;
	/** Every agent's name: an assignee that is one of them is a page to go to. */
	readonly agents: readonly string[];
	readonly error: string | null;
}

/**
 * What changes a task's page: its state (a move), its pull's checks and the bot's review, a
 * note, a question asked or answered, and a session starting or ending (its link).
 */
const WATCHED: readonly string[] = [
	"orch.task.changed",
	"orch.task.blocked",
	"orch.task.ready",
	"orch.task.merged",
	"orch.link.changed",
	"orch.question.asked",
	"orch.question.answered",
	"orch.session.started",
	"orch.session.ended",
];

/** A task's page (29.5), read when the store is made and again when any of the above moves. */
export function taskStore(ctx: UIContext, key: string): Loaded<TaskState> {
	const store = createStore<TaskState>(() => ({
		info: null,
		estimate: null,
		readiness: null,
		session: null,
		agents: [],
		error: null,
	}));
	let stopped = false;
	const reads = coalesced(
		async () => {
			const info = await ctx.call("orch_get_task", { key });
			// (The project is the feature's own answer: `orch_estimate` names it.)
			const project = asText(asObject(asObject(info).feature).project);
			// (Each of these is beside the task, not the task: one udl cannot answer — no GitHub
			// account is connected, say — leaves its own card unsaid, and the page stands.)
			const [estimate, readiness, agents, tasks] = await Promise.all([
				orNone(ctx.call("orch_estimate", { project, task: key })),
				orNone(ctx.call("orch_readiness", { key, project })),
				orNone(ctx.call("orch_list_agents", {})),
				// (The project's tasks, not its feature's: what a task unblocks may be in another
				// feature, and `needs` names a key of the project.)
				orNone(ctx.call("orch_list_tasks", { project })),
			]);
			if (!stopped) {
				store.setState({
					info: taskInfoOf(info, unblocksOf(tasks, key)),
					estimate: estimate === undefined ? null : estimateOf(estimate),
					readiness: readiness === undefined ? null : readinessOf(readiness),
					session: sessionOfTask(agents, key),
					agents: namesOf(agents),
					error: null,
				});
			}
		},
		(error) => {
			if (!stopped) {
				store.setState({ error: messageOf(error) });
			}
		},
	);
	const unsubscribes = on(ctx, WATCHED, () => {
		void reads.ask();
	});
	void reads.ask();
	return {
		store,
		refresh: () => reads.ask(),
		stop: () => {
			stopped = true;
			reads.stop();
			for (const unsubscribe of unsubscribes) {
				unsubscribe();
			}
		},
	};
}

/** Every agent's name, as `orch_list_agents` sends them. */
function namesOf(agents: JsonValue | undefined): string[] {
	return asArray(asObject(agents).items).map((item) => asText(asObject(asObject(item).agent).name));
}

/** The keys of the tasks whose `needs` name `key` (in the order udl lists them). */
function unblocksOf(tasks: JsonValue | undefined, key: string): string[] {
	const keys: string[] = [];
	for (const value of asArray(asObject(tasks).tasks)) {
		const task = asObject(value);
		if (asArray(task.needs).some((need) => asText(need) === key)) {
			keys.push(asText(task.key));
		}
	}
	return keys;
}
