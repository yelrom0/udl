import { useState } from "react";
import { messageOf } from "../json";
import { KeyLink } from "../keyLink";
import { LinkChip } from "../linkChip";
import type { Task } from "../model";
import { SizeChip } from "../sizeChip";
import type { OpenPull } from "../stores/feature";

/** What `<Tasks>` is given: a feature's tasks, and what the page may do to them. */
export interface TasksProps {
	readonly tasks: readonly Task[];
	/** The pull open on each task, by its key (Q-8: it wears it as a chip). */
	readonly pulls: Readonly<Record<string, OpenPull>>;
	/** Add one by its title; given nothing, the feature cannot grow from here. */
	readonly onAdd?: ((title: string) => Promise<void>) | undefined;
	/** Split one into these titles; given nothing, none may be split. */
	readonly onSplit?: ((key: string, into: readonly string[]) => Promise<void>) | undefined;
}

/** The lines a box was given, without the blank ones: one task a line. */
function linesOf(text: string): string[] {
	return text
		.split("\n")
		.map((line) => line.trim())
		.filter((line) => line !== "");
}

/**
 * A feature's tasks (29.5), in udl's order: each key a link to its page, its title, its state
 * and its size. A box adds a task, and a task can be split into pieces — one title a line.
 */
export function Tasks({ tasks, pulls, onAdd, onSplit }: TasksProps) {
	const [title, setTitle] = useState("");
	const [splitting, setSplitting] = useState<string | null>(null);
	const [pieces, setPieces] = useState("");
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const doing = async (work: () => Promise<void>): Promise<void> => {
		setBusy(true);
		try {
			await work();
			setError(null);
		} catch (failure) {
			setError(messageOf(failure));
		} finally {
			setBusy(false);
		}
	};
	return (
		<section className="flex flex-col gap-2">
			<h2 className="text-sm font-semibold">Tasks</h2>
			{error !== null && <p className="text-xs text-error">{error}</p>}
			{tasks.length === 0 ? (
				<p className="text-sm opacity-70">No tasks yet.</p>
			) : (
				<ol aria-label="Tasks" className="flex flex-col gap-1">
					{tasks.map((task) => (
						<li
							className="flex flex-col gap-1 rounded-lg border border-base-content/20 p-2"
							key={task.key}
						>
							<div className="flex flex-wrap items-center gap-2">
								<KeyLink kind="task" label={task.key} title={task.title} />
								<span className="min-w-0 flex-1 truncate text-sm">{task.title}</span>
								<span className="badge badge-ghost badge-sm border-base-content/20">
									{task.state}
								</span>
								<SizeChip size={task.size} />
								{pulls[task.key] !== undefined && (
									<LinkChip
										kind="pull"
										state={{}}
										title={task.title}
										url={(pulls[task.key] as OpenPull).url}
									/>
								)}
								{onSplit !== undefined && (
									<button
										className="btn btn-xs btn-ghost"
										onClick={() => {
											setSplitting(task.key);
											setPieces("");
										}}
										type="button"
									>
										Split
									</button>
								)}
							</div>
							{splitting === task.key && onSplit !== undefined && (
								<div className="flex flex-col gap-2">
									<textarea
										aria-label="Pieces"
										className="textarea textarea-bordered min-h-20 text-xs"
										onChange={(event) => {
											setPieces(event.target.value);
										}}
										placeholder={"The pieces it becomes, one title a line"}
										value={pieces}
									/>
									<div className="flex gap-2">
										<button
											className="btn btn-xs btn-primary"
											disabled={busy || linesOf(pieces).length < 2}
											onClick={() => {
												void doing(async () => {
													await onSplit(task.key, linesOf(pieces));
													setSplitting(null);
												});
											}}
											type="button"
										>
											Split task
										</button>
										<button
											className="btn btn-xs btn-ghost"
											onClick={() => {
												setSplitting(null);
											}}
											type="button"
										>
											Cancel
										</button>
									</div>
								</div>
							)}
						</li>
					))}
				</ol>
			)}
			{onAdd !== undefined && (
				<div className="flex flex-wrap items-end gap-2">
					<input
						aria-label="Title"
						className="input input-xs input-bordered w-64"
						onChange={(event) => {
							setTitle(event.target.value);
						}}
						placeholder="What the task is"
						type="text"
						value={title}
					/>
					<button
						className="btn btn-xs btn-outline"
						disabled={busy || title.trim() === ""}
						onClick={() => {
							void doing(async () => {
								await onAdd(title);
								setTitle("");
							});
						}}
						type="button"
					>
						Add task
					</button>
				</div>
			)}
		</section>
	);
}
