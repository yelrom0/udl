import type { UIContext } from "@udl/ui-sdk";
import { useState } from "react";
import { messageOf } from "../json";
import { KeyLink } from "../keyLink";
import { LinkChip } from "../linkChip";
import {
	durationOf,
	type MergeList,
	openPullsOf,
	POINTS,
	type QueueEntry,
	type Task,
} from "../model";
import { SizeChip } from "../sizeChip";

/** What the queue is given: its entries, the tasks they name (for sizes), the merge list (for
 * the current task's pull), and what it writes to. */
export interface QueueProps {
	readonly ctx: UIContext;
	readonly agent: string;
	readonly entries: readonly QueueEntry[];
	readonly tasks: readonly Task[];
	readonly merge: MergeList;
	readonly hoursPerPoint: number | null;
	readonly refresh: () => Promise<void>;
}

/** `keys` with `dragged` taken out and put where `onto` is (the order a drop makes). */
export function moved(keys: readonly string[], dragged: string, onto: string): string[] {
	const rest = keys.filter((one) => one !== dragged);
	const at = rest.indexOf(onto);
	if (at === -1) {
		return [...keys];
	}
	return [...rest.slice(0, at), dragged, ...rest.slice(at)];
}

/** `project`'s `key`, as the lookups here name a task (a key is only unique within a project). */
function whereOf(project: string, key: string): string {
	return `${project}/${key}`;
}

/** The size of `entry`'s task, when the tasks read carry it. */
function sizeOf(entry: QueueEntry, tasks: readonly Task[]): Task["size"] {
	return tasks.find((one) => one.key === entry.key && one.project === entry.project)?.size ?? null;
}

/** The points a queue's sizes come to (26.8). */
export function pointsOf(entries: readonly QueueEntry[], tasks: readonly Task[]): number {
	let points = 0;
	for (const entry of entries) {
		const size = sizeOf(entry, tasks);
		points += size === null ? 0 : POINTS[size];
	}
	return points;
}

/** What is carried: a key, and the agent whose queue it is in (a drag is one queue's). */
let carried: { agent: string; key: string } | null = null;

/**
 * An agent's queue (29.6), in udl's order: what it is on now, then what waits. Dragging a task
 * onto another puts it there — the whole order is written, as `orch_reorder_queue` takes it.
 * Each task's size, and the current one's open pull, are drawn as they are elsewhere.
 */
export function Queue({ ctx, agent, entries, tasks, merge, hoursPerPoint, refresh }: QueueProps) {
	const [over, setOver] = useState<string | null>(null);
	const pulls = openPullsOf(merge);
	const points = pointsOf(entries, tasks);
	const hours = hoursPerPoint === null ? null : points * hoursPerPoint;
	const reorder = async (key: string, onto: string): Promise<void> => {
		const dragged = entries.find((one) => one.key === key);
		const target = entries.find((one) => one.key === onto);
		// (udl reorders one project's queue: a queue that spans two is reordered within one.)
		if (dragged === undefined || target === undefined || dragged.project !== target.project) {
			ctx.toast(
				"A queue is reordered within its project: those two tasks are not in one.",
				"error",
			);
			return;
		}
		const keys = moved(
			entries.filter((one) => one.project === dragged.project).map((one) => one.key),
			key,
			onto,
		);
		try {
			await ctx.call("orch_reorder_queue", { agent, keys, project: dragged.project });
		} catch (failure) {
			ctx.toast(messageOf(failure), "error");
		}
		await refresh();
	};
	if (entries.length === 0) {
		return <p className="text-sm opacity-60">Nothing is queued for it.</p>;
	}
	return (
		<div className="flex flex-col gap-2">
			<p className="text-xs opacity-70">
				{`${points} point${points === 1 ? "" : "s"}`}
				{hours === null ? "" : ` · about ${durationOf(hoursToDuration(hours))}`}
			</p>
			<ol aria-label="Queue" className="flex flex-col gap-1">
				{entries.map((entry, at) => {
					const pull = pulls[whereOf(entry.project, entry.key)];
					return (
						<li
							aria-label={entry.key}
							className={`flex flex-wrap items-center gap-2 rounded-lg border p-2 ${
								over === entry.key ? "border-primary" : "border-base-content/20"
							}`}
							draggable
							key={entry.key}
							onDragEnd={() => {
								carried = null;
								setOver(null);
							}}
							onDragOver={(event) => {
								event.preventDefault();
								setOver(entry.key);
							}}
							onDragLeave={() => {
								setOver(null);
							}}
							onDragStart={() => {
								carried = { agent, key: entry.key };
							}}
							onDrop={(event) => {
								event.preventDefault();
								setOver(null);
								const dragged = carried;
								carried = null;
								// (A queue is one agent's: another's drop is not this one's write.)
								if (dragged === null || dragged.agent !== agent || dragged.key === entry.key) {
									return;
								}
								void reorder(dragged.key, entry.key);
							}}
						>
							<span className="text-xs opacity-60">{at === 0 ? "now" : `${at + 1}`}</span>
							<KeyLink kind="task" label={entry.key} title={entry.title} />
							<span className="min-w-0 flex-1 truncate text-sm">{entry.title}</span>
							<span className="badge badge-ghost badge-sm border-base-content/20">
								{entry.state}
							</span>
							<SizeChip size={sizeOf(entry, tasks)} />
							{pull !== undefined && (
								<LinkChip kind="pull" state={{}} title={entry.title} url={pull.url} />
							)}
						</li>
					);
				})}
			</ol>
		</div>
	);
}

/** Hours as the ISO duration udl sends, so a page says it the same way (`PT2H30M`). */
function hoursToDuration(hours: number): string {
	// (Rounded once, in minutes: 1.999 hours is 2 hours, not one hour and sixty minutes.)
	const total = Math.round(hours * 60);
	const whole = Math.floor(total / 60);
	const minutes = total % 60;
	return `PT${whole === 0 ? "" : `${whole}H`}${minutes === 0 ? "" : `${minutes}M`}${
		whole === 0 && minutes === 0 ? "0M" : ""
	}`;
}
