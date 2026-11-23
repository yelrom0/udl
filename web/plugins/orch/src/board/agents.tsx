import type { UIContext } from "@udl/ui-sdk";
import { type Agent, POINTS, type Progress, type Task } from "../model";
import { carry, carrying, drop, reordered, taskOf } from "./drops";

/** What an agent's state looks like: the dot's colour (DESIGN.md's own table). */
const DOTS: Readonly<Record<string, string>> = {
	working: "bg-success",
	waiting: "bg-info",
	limited: "bg-warning",
	offline: "bg-error",
	paused: "bg-neutral",
	idle: "bg-base-300",
};

/** What a queue's points come to, in hours: its points at the agent's model's own rate. */
function hoursOf(points: number, progress: Progress, model: string): number | null {
	const rate = progress.models.find((one) => one.model === model);
	return rate === undefined ? null : Math.round(points * rate.hours_per_point * 10) / 10;
}

/** Its keys, with the one being carried moved to where it was dropped: above the entry going
 * up, below it going down. */
export function moved(keys: readonly string[], dragged: string, onto: string): string[] {
	if (dragged === onto) {
		return [...keys];
	}
	const to = keys.indexOf(onto);
	const next = keys.filter((key) => key !== dragged);
	if (to < 0) {
		return [...next, dragged];
	}
	// (The target's own index in the list without the dragged one: before it when the drag went
	// up, after it when it went down — which is where a person dropped it either way.)
	next.splice(to, 0, dragged);
	return next;
}

/** One agent: its dot and status, its queue in order, and what that comes to (Q-8, 27.10). */
function Column({
	agent,
	tasks,
	progress,
	ctx,
	project,
	refresh,
}: {
	readonly agent: Agent;
	readonly tasks: readonly Task[];
	readonly progress: Progress;
	readonly ctx: UIContext;
	readonly project: string;
	readonly refresh: () => Promise<void>;
}) {
	const keys = agent.queue.map((entry) => entry.key);
	const points = agent.queue.reduce((sum, entry) => {
		const size = tasks.find((task) => task.key === entry.key)?.size ?? null;
		return sum + (size === null ? 0 : POINTS[size]);
	}, 0);
	const hours = hoursOf(points, progress, agent.model);
	return (
		<section
			aria-label={agent.name}
			className="flex flex-col gap-2 rounded-xl border border-base-content/20 p-3"
			onDragOver={(event) => {
				event.preventDefault();
			}}
			onDrop={(event) => {
				event.preventDefault();
				// (The column takes what is carried at its end — the queue's own entries take a
				// drop of their own, and stop it here.)
				void drop(ctx, carrying(), { kind: "agent", agent: agent.name }, project, refresh);
			}}
		>
			<div className="flex items-center gap-2">
				<span
					aria-label={agent.status === "" ? "idle" : agent.status}
					className={`inline-block size-2 rounded-full ${DOTS[agent.status] ?? DOTS.idle}`}
					role="img"
				/>
				<span className="font-medium text-sm">{agent.name}</span>
				<span className="text-xs opacity-60">{agent.status}</span>
			</div>
			<p className="text-xs opacity-70">
				{hours === null ? `${points} points` : `${points} points · ${hours} h`}
			</p>
			{agent.queue.length === 0 ? (
				<p className="text-xs opacity-60">Nothing on its queue.</p>
			) : (
				<ul aria-label={`${agent.name}'s queue`} className="flex flex-col gap-1">
					{agent.queue.map((entry, at) => (
						<li
							aria-label={entry.key}
							className="flex items-center gap-2 rounded-md border border-base-content/10 p-1"
							data-task={entry.key}
							draggable
							key={entry.key}
							onDragEnd={() => {
								carry(null);
							}}
							onDragStart={() => {
								carry({ kind: "task", key: entry.key });
							}}
							onDragOver={(event) => {
								event.preventDefault();
							}}
							onDrop={(event) => {
								// (This entry's own drop, not the column's: one write, never two.)
								event.preventDefault();
								event.stopPropagation();
								const what = carrying();
								if (what === null) {
									return;
								}
								const key = taskOf(what);
								if (key === entry.key) {
									return;
								}
								if (keys.includes(key)) {
									void reordered(ctx, agent.name, moved(keys, key, entry.key), project, refresh);
								} else {
									// (A task from another queue, or a suggestion: it is assigned here.)
									void drop(
										ctx,
										what,
										{ kind: "agent", agent: agent.name, position: at },
										project,
										refresh,
									);
								}
							}}
						>
							<span className="font-mono text-xs">{entry.key}</span>
							<span className="min-w-0 flex-1 truncate text-xs">{entry.title}</span>
						</li>
					))}
				</ul>
			)}
		</section>
	);
}

/** The agents pane: a column per agent, its current task on top. */
export function AgentsPane({
	agents,
	tasks,
	progress,
	ctx,
	project,
	refresh,
}: {
	readonly agents: readonly Agent[];
	readonly tasks: readonly Task[];
	readonly progress: Progress;
	readonly ctx: UIContext;
	readonly project: string;
	readonly refresh: () => Promise<void>;
}) {
	return (
		<section aria-label="Agents" className="flex flex-col gap-3">
			{agents.length === 0 ? (
				<p className="text-xs opacity-60">No agents yet.</p>
			) : (
				agents.map((agent) => (
					<Column
						agent={agent}
						ctx={ctx}
						key={agent.name}
						progress={progress}
						project={project}
						refresh={refresh}
						tasks={tasks}
					/>
				))
			)}
		</section>
	);
}
