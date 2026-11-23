import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useFeature } from "../hooks";
import { Page, Said } from "../page";
import { Links } from "../parts/links";
import { Notes } from "../parts/notes";
import { SpecCard } from "../parts/spec";
import { Tasks } from "./tasks";

/** A forecast's day, in the reader's own clock: udl gives an instant, the page gives a day. */
function dayOf(forecast: string): string {
	const when = new Date(forecast);
	return Number.isNaN(when.getTime())
		? forecast
		: when.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** When what is left of the feature is likely done, in words. */
function likely(forecast: string | null): string {
	return forecast === null ? "no forecast yet" : `likely ${dayOf(forecast)}`;
}

/**
 * A feature (29.5): its spec, its tasks in order, its points and forecast, what points at it,
 * and its notes. The spec and the tasks are editable here — the feature is where work is
 * planned, so it is where a task is added or split.
 */
export const FeaturePage: Component<RouteProps> = ({ ctx, params }) => {
	const key = params.key ?? "";
	const { info, points, error, refresh } = useFeature(ctx, key);
	const feature = info?.feature ?? null;
	const reading = info === null && error === null;
	return (
		<Page title={`Feature ${key}`}>
			{error !== null && <p className="text-error">{error}</p>}
			{reading && <Said>Reading the feature…</Said>}
			{feature !== null && info !== null && (
				<div className="grid gap-4 lg:grid-cols-3">
					<div className="flex flex-col gap-4 lg:col-span-2">
						<div className="flex flex-wrap items-baseline gap-2">
							<h2 className="text-xl font-semibold">{feature.title}</h2>
							<span className="badge badge-ghost badge-sm border-base-content/20">
								{feature.status}
							</span>
						</div>
						<SpecCard
							onSave={async (spec) => {
								await ctx.call("orch_set_feature", { key, spec });
								await refresh();
							}}
							spec={info.spec}
							title="Spec"
						/>
						<Tasks
							onAdd={async (title) => {
								// (No key: udl takes the feature's next one.)
								await ctx.call("orch_set_task", { feature: key, title });
								await refresh();
							}}
							onSplit={async (task, into) => {
								await ctx.call("orch_split_task", {
									key: task,
									into: into.map((title) => ({ title })),
								});
								await refresh();
							}}
							tasks={info.tasks}
						/>
						<Notes
							notes={info.notes}
							onAdd={async (note) => {
								await ctx.call("orch_add_note", {
									owner_kind: "feature",
									owner: key,
									...note,
								});
								await refresh();
							}}
						/>
					</div>
					<div className="flex flex-col gap-4">
						<section className="flex flex-col gap-1 rounded-lg border border-base-content/20 p-3 shadow-sm">
							<h2 className="text-sm font-semibold">Progress</h2>
							<p className="text-2xl font-semibold">
								{`${points.merged} of ${points.points} points`}
							</p>
							<p className="text-xs opacity-70">{likely(points.forecast)}</p>
						</section>
						<Links
							links={info.links}
							onAdd={async (link) => {
								await ctx.call("orch_add_link", {
									owner_kind: "feature",
									owner: key,
									...link,
								});
								await refresh();
							}}
						/>
					</div>
				</div>
			)}
		</Page>
	);
};
