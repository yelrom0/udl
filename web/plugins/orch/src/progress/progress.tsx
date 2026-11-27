import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useProgress } from "../hooks";
import { KeyLink } from "../keyLink";
import { asSize, type FeatureProgress, type ModelRow, type RecentTask } from "../model";
import { Page, Said } from "../page";
import { SizeChip } from "../sizeChip";
import { BurnUp, dayLabel, longDayLabel } from "./burnUp";

/** The page's own route, which its project and day are written onto (`ctx.navigate`'s path of
 * the page's: the shell puts its own base in front of it). */
export const PROGRESS = "/p/orch/progress";

/** The days a person may start the burn-up from, before udl's own thirty. */
const SINCE_DAYS: readonly number[] = [14, 90];

/** A card with a clear edge (DESIGN.md's rule: a base-content 20 % border and a shadow). */
const CARD = "card border border-base-content/20 bg-base-100 shadow-sm";

/** A duration, in hours (`PT4H24M` is `4.4`). */
function hoursOf(duration: string): number {
	const match = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/.exec(duration);
	if (match === null) {
		return 0;
	}
	const [, days, hours, minutes, seconds] = match;
	return (
		Number(days ?? 0) * 24 +
		Number(hours ?? 0) +
		Number(minutes ?? 0) / 60 +
		Number(seconds ?? 0) / 3600
	);
}

/** Hours, the way the page writes them (`4.4 h`). */
function hours(hours: number): string {
	return `${hours.toFixed(1)} h`;
}

/** A ratio, the way the page writes it (`1.16×`). */
function times(ratio: number): string {
	return `${ratio.toFixed(2)}×`;
}

/** What a ratio's colour says: amber when the work ran over, green when it came in under. */
function ratioClass(ratio: number): string {
	if (ratio > 1.25) {
		return "text-warning font-semibold";
	}
	return ratio < 0.9 ? "text-success font-semibold" : "";
}

/** The page's path with the project or the day changed: a cleared one leaves the query. */
function progressPath(
	current: { readonly project: string; readonly since: string },
	change: { readonly project?: string; readonly since?: string },
): string {
	const next = { ...current, ...change };
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(next)) {
		if (value !== "") {
			query.set(key, value);
		}
	}
	const written = query.toString();
	return written === "" ? PROGRESS : `${PROGRESS}?${written}`;
}

/** One of the four numbers at the top: what it is, the number, and what it is made of. */
function Stat({
	label,
	value,
	note,
	children,
}: {
	readonly label: string;
	readonly value: string;
	readonly note: string;
	readonly children?: React.ReactNode;
}) {
	return (
		<fieldset className={`${CARD} p-4`}>
			<legend className="px-1 text-xs uppercase tracking-wide opacity-60">{label}</legend>
			<p className="text-3xl font-semibold">{value}</p>
			<p className="text-xs opacity-70">{note}</p>
			{children}
		</fieldset>
	);
}

/** A card with its title and what sits beside it on the right. */
function Card({
	name,
	title,
	aside,
	children,
}: {
	readonly name: string;
	readonly title: string;
	readonly aside?: string;
	readonly children: React.ReactNode;
}) {
	return (
		<section aria-label={name} className={CARD}>
			<div className="card-body gap-3 p-4">
				<header className="flex flex-wrap items-baseline gap-2">
					<h2 className="card-title text-sm uppercase tracking-wide">{title}</h2>
					{aside !== undefined && <span className="ml-auto text-xs opacity-60">{aside}</span>}
				</header>
				{children}
			</div>
		</section>
	);
}

/** A feature's bar: the points merged, of its points. */
function FeatureRow({ feature }: { readonly feature: FeatureProgress }) {
	const share = feature.points === 0 ? 0 : (feature.merged / feature.points) * 100;
	const done = feature.points > 0 && feature.merged >= feature.points;
	return (
		<li className="flex flex-col gap-1">
			<div className="flex items-baseline gap-2 text-sm">
				<KeyLink kind="feature" label={feature.key} title={feature.title} />
				<span className="truncate">{feature.title}</span>
				<span className="ml-auto text-xs opacity-70">{`${feature.merged}/${feature.points}`}</span>
				{done ? (
					<span className="badge badge-success badge-sm">done</span>
				) : (
					feature.forecast !== null && (
						<span className="badge badge-ghost badge-sm">
							{longDayLabel(new Date(feature.forecast.likely))}
						</span>
					)
				)}
			</div>
			<progress
				className="progress progress-primary h-1.5"
				max={100}
				value={share}
				aria-label={`${feature.key}: points merged`}
			/>
		</li>
	);
}

/** One merged task: what it was estimated at, what it took, and how far off that was. */
function RecentRow({ task }: { readonly task: RecentTask }) {
	const estimated = hoursOf(task.estimate);
	const actual = hoursOf(task.actual);
	const ratio = estimated === 0 ? null : actual / estimated;
	return (
		<tr>
			<td>
				<KeyLink kind="task" label={task.key} title={task.title} />{" "}
				<span className="opacity-80">{task.title}</span>
			</td>
			<td>
				<SizeChip size={asSize(task.size)} />
			</td>
			<td className="text-right tabular-nums">{hours(estimated)}</td>
			<td className="text-right tabular-nums">{hours(actual)}</td>
			<td className={`text-right tabular-nums ${ratio === null ? "" : ratioClass(ratio)}`}>
				{ratio === null ? "–" : times(ratio)}
			</td>
		</tr>
	);
}

/** One model's learned hours: what a point costs it, and what each size came to. */
function ModelRowView({ row }: { readonly row: ModelRow }) {
	return (
		<tr>
			<td>
				<p className="font-mono text-sm">{row.model}</p>
				<p className="text-xs opacity-60">{`${row.runner} · ${row.tasks} tasks`}</p>
			</td>
			<td className="text-right tabular-nums">{row.hours_per_point.toFixed(1)}</td>
			{["S", "M", "L", "XL"].map((size) => (
				<td className="hidden text-right tabular-nums sm:table-cell" key={size}>
					{row.hours_per_size[size] === undefined ? "–" : row.hours_per_size[size].toFixed(1)}
				</td>
			))}
		</tr>
	);
}

/**
 * The progress page (29.11): the points merged of all of them, when the rest is forecast to
 * land and how sure that is, the burn-up itself, each feature's share and forecast, the tasks
 * merged lately against what they were estimated at, and what a point has cost each model.
 *
 * The project and the day the burn-up starts are in the URL, so a view is a page someone can
 * send. A page opened without them is the only project, from udl's own thirty days.
 */
export const ProgressPage: Component<RouteProps> = ({ ctx, search }) => {
	const project = search.project ?? "";
	const since = search.since ?? "";
	const { projects, report, loaded, error } = useProgress(ctx, project, since);
	const now = new Date();
	const sinceChoices = [
		{ value: "", label: `Since ${dayLabel(new Date(now.getTime() - 30 * 86_400_000))}` },
		...SINCE_DAYS.map((days) => ({
			value: new Date(now.getTime() - days * 86_400_000).toISOString().slice(0, 10),
			label: `Since ${dayLabel(new Date(now.getTime() - days * 86_400_000))}`,
		})),
	];
	// (What the pickers show and what a change carries: the project the page is of — the one it
	// was opened with, or the one udl took for an empty URL — and the day it starts at.)
	const shown = { project: project !== "" ? project : (report?.project ?? ""), since };
	const merged = report?.merged ?? 0;
	const scope = report?.points ?? 0;
	// (What was merged before the series' first day: a window cannot hold it, and the line that
	// starts at nothing would say the work began when the window did.)
	const baseline = Math.max(
		0,
		merged - (report?.series ?? []).reduce((total, one) => total + one.merged, 0),
	);
	const left = Math.max(0, scope - merged);
	const forecast = report?.forecast;
	const spread =
		forecast === undefined || forecast.late === "" || forecast.early === ""
			? 0
			: Math.round(
					Math.abs(Date.parse(forecast.late) - Date.parse(forecast.early)) / 2 / 86_400_000,
				);
	return (
		<Page title="Progress">
			<div className="flex flex-wrap items-end justify-between gap-2">
				<Said>
					Points merged over time, and when the rest should land, learned from how long each size of
					task really took.
				</Said>
				<div className="flex flex-wrap items-end gap-2">
					<label className="flex flex-col gap-1 text-xs">
						Project
						<select
							aria-label="Project"
							className="select select-sm select-bordered"
							onChange={(event) => {
								ctx.navigate?.(progressPath(shown, { project: event.target.value }));
							}}
							value={shown.project}
						>
							{projects.map((one) => (
								<option key={one} value={one}>
									{`project ${one}`}
								</option>
							))}
						</select>
					</label>
					<label className="flex flex-col gap-1 text-xs">
						Since
						<select
							aria-label="Since"
							className="select select-sm select-bordered"
							onChange={(event) => {
								ctx.navigate?.(progressPath(shown, { since: event.target.value }));
							}}
							value={since}
						>
							{sinceChoices.map((choice) => (
								<option key={choice.value} value={choice.value}>
									{choice.label}
								</option>
							))}
						</select>
					</label>
				</div>
			</div>
			{error !== null && <p className="text-error">{error}</p>}
			{!loaded && <Said>Reading the progress…</Said>}
			{loaded && error === null && report === null && (
				<Said>No project yet, so there is nothing to measure.</Said>
			)}
			{loaded && error === null && report !== null && forecast !== undefined && (
				<>
					<div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
						<Stat
							label="Merged"
							note={`of ${scope} points · ${scope === 0 ? 0 : Math.round((merged / scope) * 100)} %`}
							value={`${merged}`}
						>
							<progress
								aria-label="Points merged"
								className="progress progress-primary mt-2 h-1.5"
								max={Math.max(scope, 1)}
								value={merged}
							/>
						</Stat>
						<Stat
							label="Forecast"
							note={`±${spread} days · ${left} points left`}
							value={forecast.likely === "" ? "–" : longDayLabel(new Date(forecast.likely))}
						/>
						<Stat
							label="Pace"
							note="points merged a day, over the last 7"
							value={`${Number.isInteger(report.pace) ? report.pace : report.pace.toFixed(1)} pts/day`}
						/>
						<Stat
							label="Estimates"
							note="actual ÷ estimate, last 20"
							value={times(report.estimate_ratio)}
						/>
					</div>
					<div className="grid gap-4 xl:grid-cols-3">
						<div className="flex flex-col gap-4 xl:col-span-2">
							<Card aside={`project ${report.project}`} name="Burn-up" title="Burn-up">
								<BurnUp forecast={report.forecast} series={report.series} today={now} />
							</Card>
							<Card aside="estimate vs actual" name="Recently merged" title="Recently merged">
								{report.recent.length === 0 ? (
									<Said>Nothing sized has merged yet.</Said>
								) : (
									<table className="table table-sm">
										<thead>
											<tr className="text-xs uppercase opacity-60">
												<th>Task</th>
												<th>Size</th>
												<th className="text-right">Est.</th>
												<th className="text-right">Actual</th>
												<th className="text-right">Ratio</th>
											</tr>
										</thead>
										<tbody>
											{report.recent.map((one) => (
												<RecentRow key={one.key} task={one} />
											))}
										</tbody>
									</table>
								)}
							</Card>
						</div>
						<div className="flex flex-col gap-4">
							<Card aside="points merged" name="By feature" title="By feature">
								{report.features.length === 0 ? (
									<Said>No feature has points yet.</Said>
								) : (
									<ul className="flex flex-col gap-3">
										{report.features.map((one) => (
											<FeatureRow feature={one} key={one.key} />
										))}
									</ul>
								)}
							</Card>
							<Card aside="hours per size" name="Hours per point" title="Hours per point, learned">
								{report.models.length === 0 ? (
									<Said>Nothing has been learned yet.</Said>
								) : (
									<table className="table table-sm">
										<thead>
											<tr className="text-xs uppercase opacity-60">
												<th>Model</th>
												<th className="text-right">H/pt</th>
												{["S", "M", "L", "XL"].map((size) => (
													<th className="hidden text-right sm:table-cell" key={size}>
														{size}
													</th>
												))}
											</tr>
										</thead>
										<tbody>
											{report.models.map((one) => (
												<ModelRowView key={`${one.model}:${one.runner}`} row={one} />
											))}
										</tbody>
									</table>
								)}
								<p className="text-xs opacity-60">
									Sizes are 1, 3, 5 and 8 points. Hours per point are learned per model from its
									merged tasks, once it has five; until then the project's average is used.
								</p>
							</Card>
						</div>
					</div>
				</>
			)}
		</Page>
	);
};
