import type { UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asArray, asObject, asText, messageOf } from "../json";
import { type ProgressReport, progressReportOf } from "../model";
import type { Loaded } from "./inbox";
import { coalesced, on } from "./once";

/** What the progress page holds: the projects it may show, and the one it shows. */
export interface ProgressState {
	/** Every project udl knows, by name (the page's picker). */
	readonly projects: readonly string[];
	/** The project the numbers are of (what udl answered for, or the one asked for). */
	readonly project: string;
	/** What it is of: its burn-up, forecast, features, models and recently merged tasks. */
	readonly report: ProgressReport | null;
	readonly loaded: boolean;
	readonly error: string | null;
}

/**
 * What moves what the page draws (28.5): a task changing state, a pull readied or merged, a link
 * refreshed, and an agent's session starting, ending or failing — a merge is what a burn-up
 * counts, and a session's end is when its task's working time is known.
 */
const WATCHED: readonly string[] = [
	"orch.task.changed",
	"orch.task.blocked",
	"orch.task.ready",
	"orch.task.merged",
	"orch.link.changed",
	"orch.session.started",
	"orch.session.ended",
	"orch.session.failed",
	"orch.agent.limited",
];

/**
 * A project's progress (29.11), read from `orch_progress` (27.10).
 *
 * `project` names the one to show, and an empty one takes the first udl lists — a page opened
 * without a project in its URL is the only project, while udl has one. `since` is the day the
 * burn-up starts (`YYYY-MM-DD`), and an empty one leaves udl's own thirty days.
 */
export function progressStore(
	ctx: UIContext,
	project: string,
	since: string,
): Loaded<ProgressState> {
	const store = createStore<ProgressState>(() => ({
		projects: [],
		project,
		report: null,
		loaded: false,
		error: null,
	}));
	let stopped = false;
	const reads = coalesced(
		async () => {
			const listed = await ctx.call("orch_list_projects", {});
			const projects = asArray(asObject(listed).projects)
				.map((one) => asText(asObject(one).name))
				.filter((name) => name !== "");
			const wanted = project !== "" ? project : (projects[0] ?? "");
			const asked = since === "" ? { project: wanted } : { project: wanted, since };
			const report = wanted === "" ? undefined : await ctx.call("orch_progress", asked);
			if (!stopped) {
				store.setState({
					projects,
					project: wanted,
					report: report === undefined ? null : progressReportOf(report),
					loaded: true,
					error: null,
				});
			}
		},
		(error) => {
			if (!stopped) {
				store.setState({ loaded: true, error: messageOf(error) });
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
