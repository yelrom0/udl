import { LinkChip } from "../linkChip";
import type { Feature, MergeList, Progress, Task } from "../model";
import { SizeChip } from "../sizeChip";

/** Where a task's own page is (`29.5` draws it). */
function pageOf(key: string): string {
	return `/ui/p/orch/tasks/${encodeURIComponent(key)}`;
}

/** The pull a task's card shows while it is open: its URL, from the merge list (Q-8). */
function pullOf(merge: MergeList, task: string): string | null {
	for (const row of [...merge.ready, ...merge.coming, ...merge.held]) {
		if (row.task === task && row.url !== "") {
			return row.url;
		}
	}
	return null;
}

/** One task, as the features pane draws it: its key, what it is, where it is, its size, its PR. */
function TaskCard({ task, merge }: { readonly task: Task; readonly merge: MergeList }) {
	const pull = pullOf(merge, task.key);
	return (
		<article
			aria-label={`${task.key} ${task.title}`}
			className="flex flex-wrap items-center gap-2 rounded-lg border border-base-content/20 bg-base-100 p-2"
		>
			<a className="font-mono text-sm link link-hover" href={pageOf(task.key)}>
				{task.key}
			</a>
			<span className="min-w-0 flex-1 truncate text-sm">{task.title}</span>
			<SizeChip size={task.size} />
			<span className="badge badge-sm badge-ghost">{task.state}</span>
			{pull !== null && <LinkChip kind="pull" url={pull} />}
		</article>
	);
}

/** One feature: its bar (Q-8), and its tasks. */
function FeatureBlock({
	feature,
	tasks,
	merge,
	progress,
}: {
	readonly feature: Feature;
	readonly tasks: readonly Task[];
	readonly merge: MergeList;
	readonly progress: Progress;
}) {
	const points = progress.features.find((one) => one.key === feature.key);
	const merged = points?.merged ?? 0;
	const total = points?.points ?? 0;
	const mine = tasks.filter((task) => task.feature === feature.key);
	return (
		<section
			aria-label={`${feature.key} · ${feature.title}`}
			className="flex flex-col gap-2 rounded-xl border border-base-content/20 p-3"
		>
			<details open>
				<summary className="flex flex-wrap items-baseline gap-2">
					<h2 className="text-base font-semibold">
						<span className="font-mono">{feature.key}</span> <span>{feature.title}</span>
					</h2>
					<span className="text-xs opacity-70">{`${merged} of ${total} points`}</span>
				</summary>
				{total > 0 && (
					<progress
						aria-label={`${feature.key} merged`}
						className="progress progress-success mt-2 w-full"
						max={total}
						value={merged}
					/>
				)}
				<div className="mt-2 flex flex-col gap-2">
					{mine.length === 0 ? (
						<p className="text-xs opacity-60">No tasks match.</p>
					) : (
						mine.map((task) => <TaskCard key={task.key} merge={merge} task={task} />)
					)}
				</div>
			</details>
		</section>
	);
}

/** The features pane: every feature, collapsible, with its bar and its tasks. */
export function FeaturesPane({
	features,
	tasks,
	merge,
	progress,
}: {
	readonly features: readonly Feature[];
	readonly tasks: readonly Task[];
	readonly merge: MergeList;
	readonly progress: Progress;
}) {
	return (
		<section aria-label="Features" className="flex flex-col gap-3">
			{features.map((feature) => (
				<FeatureBlock
					feature={feature}
					key={feature.key}
					merge={merge}
					progress={progress}
					tasks={tasks}
				/>
			))}
		</section>
	);
}
