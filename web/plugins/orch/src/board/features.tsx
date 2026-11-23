import { useState } from "react";
import { LinkChip } from "../linkChip";
import type { Agent, Feature, MergeList, Progress, Task } from "../model";
import { SizeChip } from "../sizeChip";
import { carry } from "./drops";

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

/** "Assign to…", for a keyboard and a pointer alike: the same call a drop makes. */
function AssignMenu({
	agents,
	onAssign,
}: {
	readonly agents: readonly Agent[];
	readonly onAssign: ((agent: string) => void) | undefined;
}) {
	const [open, setOpen] = useState(false);
	return (
		<div className="relative">
			<button
				aria-expanded={open}
				aria-haspopup="menu"
				className="btn btn-ghost btn-xs"
				onClick={() => {
					setOpen((was) => !was);
				}}
				type="button"
			>
				Assign to…
			</button>
			{open && (
				<div
					className="menu menu-sm absolute right-0 z-10 rounded-box bg-base-100 shadow"
					role="menu"
				>
					{agents.map((agent) => (
						<button
							key={agent.name}
							onClick={() => {
								setOpen(false);
								onAssign?.(agent.name);
							}}
							role="menuitem"
							type="button"
						>
							{agent.name}
						</button>
					))}
				</div>
			)}
		</div>
	);
}

/** One task, as the features pane draws it: its key, what it is, where it is, its size, its PR. */
function TaskCard({
	task,
	merge,
	agents,
	onAssign,
}: {
	readonly task: Task;
	readonly merge: MergeList;
	readonly agents: readonly Agent[];
	readonly onAssign: ((task: string, agent: string) => void) | undefined;
}) {
	const pull = pullOf(merge, task.key);
	return (
		<article
			aria-label={`${task.key} ${task.title}`}
			className="flex flex-wrap items-center gap-2 rounded-lg border border-base-content/20 bg-base-100 p-2"
			draggable
			onDragStart={() => {
				carry({ kind: "task", key: task.key });
			}}
		>
			<a className="font-mono text-sm link link-hover" href={pageOf(task.key)}>
				{task.key}
			</a>
			<span className="min-w-0 flex-1 truncate text-sm">{task.title}</span>
			<SizeChip size={task.size} />
			<span className="badge badge-sm badge-ghost">{task.state}</span>
			{pull !== null && <LinkChip kind="pull" url={pull} />}
			<AssignMenu
				agents={agents}
				onAssign={(agent) => {
					onAssign?.(task.key, agent);
				}}
			/>
		</article>
	);
}

/** One feature: its bar (Q-8), and its tasks. */
function FeatureBlock({
	feature,
	tasks,
	merge,
	progress,
	agents,
	onAssign,
}: {
	readonly feature: Feature;
	readonly tasks: readonly Task[];
	readonly merge: MergeList;
	readonly progress: Progress;
	readonly agents: readonly Agent[];
	readonly onAssign: ((task: string, agent: string) => void) | undefined;
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
						mine.map((task) => (
							<TaskCard
								agents={agents}
								key={task.key}
								merge={merge}
								onAssign={onAssign}
								task={task}
							/>
						))
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
	agents,
	onAssign,
	onDrop,
}: {
	readonly features: readonly Feature[];
	readonly tasks: readonly Task[];
	readonly merge: MergeList;
	readonly progress: Progress;
	readonly agents: readonly Agent[];
	readonly onAssign: ((task: string, agent: string) => void) | undefined;
	readonly onDrop: () => void;
}) {
	return (
		<section
			aria-label="Features"
			className="flex flex-col gap-3"
			onDragOver={(event) => {
				event.preventDefault();
			}}
			onDrop={(event) => {
				event.preventDefault();
				onDrop();
			}}
		>
			{features.map((feature) => (
				<FeatureBlock
					agents={agents}
					feature={feature}
					key={feature.key}
					merge={merge}
					onAssign={onAssign}
					progress={progress}
					tasks={tasks}
				/>
			))}
		</section>
	);
}
