import { type Agent, POINTS, type Progress, type Task } from "../model";
import { carry, carrying } from "./drops";

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

/** Its keys, with the one being carried moved to the position it was dropped at. */
function moved(keys: readonly string[], dragged: string, onto: string): string[] {
	const next = keys.filter((key) => key !== dragged);
	const at = keys.indexOf(onto);
	next.splice(at < 0 ? next.length : at, 0, dragged);
	return next;
}

/** One agent: its dot and status, its queue in order, and what that comes to (Q-8, 27.10). */
function Column({
	agent,
	tasks,
	progress,
	onDrop,
	onReorder,
}: {
	readonly agent: Agent;
	readonly tasks: readonly Task[];
	readonly progress: Progress;
	readonly onDrop: (agent: string) => void;
	readonly onReorder: (agent: string, keys: readonly string[]) => void;
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
				onDrop(agent.name);
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
					{agent.queue.map((entry) => (
						<li
							aria-label={entry.key}
							className="flex items-center gap-2 rounded-md border border-base-content/10 p-1"
							data-task={entry.key}
							draggable
							key={entry.key}
							onDragStart={() => {
								carry({ kind: "task", key: entry.key });
							}}
							onDragOver={(event) => {
								event.preventDefault();
							}}
							onDrop={(event) => {
								event.preventDefault();
								const what = carrying();
								if (what === null || what.kind !== "task" || what.key === entry.key) {
									return;
								}
								onReorder(agent.name, moved(keys, what.key, entry.key));
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
	onDrop,
	onReorder,
}: {
	readonly agents: readonly Agent[];
	readonly tasks: readonly Task[];
	readonly progress: Progress;
	readonly onDrop: (agent: string) => void;
	readonly onReorder: (agent: string, keys: readonly string[]) => void;
}) {
	return (
		<section aria-label="Agents" className="flex flex-col gap-3">
			{agents.length === 0 ? (
				<p className="text-xs opacity-60">No agents yet.</p>
			) : (
				agents.map((agent) => (
					<Column
						agent={agent}
						key={agent.name}
						onDrop={onDrop}
						onReorder={onReorder}
						progress={progress}
						tasks={tasks}
					/>
				))
			)}
		</section>
	);
}
