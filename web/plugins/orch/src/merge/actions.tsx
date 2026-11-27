/**
 * -----------
 * - actions.tsx -
 * -----------
 *
 * What only a person can do (29.8): now, after a task merges, and later — each with what came
 * of it when it is finished. The rows are 28.2's, and finishing one calls `orch_finish_action`
 * with the result, which udl writes as a note on the task it is about.
 */

import type { UIContext } from "@udl/ui-sdk";
import { useState } from "react";
import type { Action } from "../model";

/** What the page is given: the actions, the project, and what it does with a finish. */
export interface ActionsProps {
	ctx: UIContext;
	readonly actions: readonly Action[];
	project: string;
	/** Read it again: what the page asks after a finish, to see the list without a reload. */
	refresh(): Promise<void>;
}

/** One thing only a person can do, with a result box and its Done (29.8). */
function Row({
	action,
	failed,
	finishing,
	onDone,
}: {
	readonly action: Action;
	readonly failed: boolean;
	readonly finishing: boolean;
	onDone(key: string, result: string): void;
}) {
	const [result, setResult] = useState("");

	return (
		<li
			aria-invalid={failed || undefined}
			className="flex flex-col gap-2 border-base-content/10 border-b py-2 text-sm last:border-b-0"
		>
			<span className="flex flex-wrap items-baseline gap-2">
				<span className="badge badge-ghost badge-sm">{action.key}</span>
				<span className="min-w-0 flex-1">{action.title}</span>
				{action.text !== "" && <span className="text-xs opacity-70">{action.text}</span>}
			</span>
			{action.task !== null && (
				<a className="link text-xs" href={`/ui/p/orch/tasks/${action.task}`}>
					{action.task}
				</a>
			)}
			<form
				className="flex flex-wrap items-end gap-2"
				onSubmit={(event) => {
					event.preventDefault();
					if (result.trim() === "" || finishing) {
						return;
					}
					onDone(action.key, result.trim());
				}}
			>
				<label className="flex min-w-0 flex-1 flex-col gap-1 text-xs">
					Result
					<textarea
						aria-label={`Result for ${action.key}`}
						className="textarea textarea-bordered w-full"
						onChange={(event) => {
							setResult(event.target.value);
						}}
						rows={2}
						value={result}
					/>
				</label>
				<button className="btn btn-sm" disabled={finishing || result.trim() === ""} type="submit">
					{finishing ? "Finishing…" : "Done"}
				</button>
			</form>
		</li>
	);
}

/**
 * Now, then after, then later (29.8): each thing a person can do, with a result box and its
 * Done. What is finished leaves the list — udl writes the result on the task and moves the
 * action on — so the page asks for the list again rather than dropping the row itself.
 */
export function Actions({ ctx, actions, project, refresh }: ActionsProps) {
	const [finishing, setFinishing] = useState<string | null>(null);
	const [failed, setFailed] = useState<string | null>(null);
	const [said, setSaid] = useState<string | null>(null);

	const finish = async (key: string, result: string): Promise<void> => {
		setFinishing(key);
		setFailed(null);
		setSaid(null);
		try {
			await ctx.call("orch_finish_action", { project, key, result });
			await refresh();
		} catch (failure) {
			setFailed(key);
			setSaid(failure instanceof Error ? failure.message : String(failure));
		} finally {
			setFinishing(null);
		}
	};

	return (
		<div className="flex flex-col gap-3">
			{failed !== null && said !== null && said !== "" && (
				<p className="alert alert-error alert-soft text-sm" role="alert">
					{failed}: {said}
				</p>
			)}
			{actions.length === 0 && <p className="text-sm opacity-70">Nothing needs a person.</p>}
			{GROUPS.map((group) => {
				const mine = actions.filter((one) => one.when === group.when);
				if (mine.length === 0) {
					return null;
				}
				return (
					<section className="flex flex-col gap-1" key={group.when}>
						<h2 className="text-xs font-semibold uppercase tracking-wide opacity-60">
							{group.title}
						</h2>
						<ul className="flex flex-col">
							{mine.map((one) => (
								<Row
									action={one}
									failed={failed === one.key}
									finishing={finishing === one.key}
									key={one.key}
									onDone={(key, result) => {
										void finish(key, result);
									}}
								/>
							))}
						</ul>
					</section>
				);
			})}
		</div>
	);
}

/** The three groups an action can be in (28.2), in the order a person reads them. */
const GROUPS: readonly { readonly when: string; readonly title: string }[] = [
	{ when: "now", title: "Now" },
	{ when: "after", title: "After" },
	{ when: "later", title: "Later" },
];
