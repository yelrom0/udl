import type { JsonValue, UIContext } from "@udl/ui-sdk";
import { createStore } from "zustand/vanilla";
import { asArray, asNumber, asObject, asText, messageOf } from "../json";
import { type FeatureInfo, featureInfoOf } from "../model";
import type { Loaded } from "./inbox";
import { coalesced, on, orNone } from "./once";

/** A feature's own share of the project's progress (27.10, per Q-8). */
export interface FeaturePoints {
	readonly points: number;
	readonly merged: number;
	readonly forecast: string | null;
}

/** What a feature's page holds: the feature, its progress row, and the project's pace. */
export interface FeatureState {
	readonly info: FeatureInfo | null;
	readonly points: FeaturePoints;
	readonly pace: number;
	readonly error: string | null;
}

/**
 * What changes a feature's page: a task of it moving (its totals, its list), a pull readied or
 * merged (its links and its points), a link refreshed, and a note or a spec written.
 */
const WATCHED: readonly string[] = [
	"orch.task.changed",
	"orch.task.blocked",
	"orch.task.ready",
	"orch.task.merged",
	"orch.link.changed",
	"orch.project.changed",
];

/** A feature's page (29.5), read when the store is made and again when any of the above moves. */
export function featureStore(ctx: UIContext, key: string): Loaded<FeatureState> {
	const store = createStore<FeatureState>(() => ({
		info: null,
		points: { points: 0, merged: 0, forecast: null },
		pace: 0,
		error: null,
	}));
	let stopped = false;
	const reads = coalesced(
		async () => {
			const info = await ctx.call("orch_get_feature", { key });
			// (The project's progress, for its own row: the page does not read the whole project's
			// burn-up — 29.11's page does. It is asked for the feature's project, which is the
			// feature's own answer.)
			const project = asText(asObject(asObject(info).feature).project);
			// (The progress is beside the feature: if udl cannot answer it, the page says so of its
			// own card and draws the rest.)
			const progress = await orNone(ctx.call("orch_progress", { project }));
			if (!stopped) {
				const feature = featureInfoOf(info);
				store.setState({
					info: feature,
					points: pointsOf(progress, feature.feature.key),
					pace: asNumber(asObject(progress).pace),
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

/** One feature's row of `orch_progress`, by its key. */
function pointsOf(progress: JsonValue | undefined, key: string): FeaturePoints {
	for (const value of asArray(asObject(progress).features)) {
		const row = asObject(value);
		if (asText(row.key) !== key) {
			continue;
		}
		return {
			points: asNumber(row.points),
			merged: asNumber(row.merged),
			forecast: asText(asObject(row.forecast).likely) || null,
		};
	}
	return { points: 0, merged: 0, forecast: null };
}
