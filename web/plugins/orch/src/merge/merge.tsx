/**
 * -------------
 * - merge.tsx -
 * -------------
 *
 * What is ready to merge, coming soon, held, and what merged lately (29.8) — with a merge
 * button that asks which pull it is before it calls `orch_merge` (28.3). The rows are 29.3's
 * card, drawn again here in full width rather than the inbox's strip.
 */

import type { UIContext } from "@udl/ui-sdk";
import { useState } from "react";
import type { MergeList, MergeRow } from "../model";

/** What the page is given: the list, the project, and what it does with a merge. */
export interface MergeProps {
	ctx: UIContext;
	merge: MergeList;
	project: string;
	/** Read it again: what the page asks after a merge, to see the list without a reload. */
	refresh(): Promise<void>;
}

/** A task or a feature, linked to its own page (29.5's). */
function Key({ kind, name }: { readonly kind: string; readonly name: string }) {
	return (
		<a className="link" href={`/ui/p/orch/${kind}/${name}`}>
			{name}
		</a>
	);
}

/** One pull, in full: its chip, its title, its checks and the bot's word, and its task. */
function Row({
	row,
	mergeable,
	busy,
	failed,
	onMerge,
}: {
	readonly row: MergeRow;
	/** Whether this row may be merged (only `ready` rows are). */
	readonly mergeable: boolean;
	/** Whether a merge is in flight (any row's: only one at a time). */
	readonly busy: boolean;
	readonly failed: boolean;
	onMerge(row: MergeRow): void;
}) {
	return (
		<li
			aria-invalid={failed || undefined}
			className="flex flex-wrap items-center gap-2 border-base-content/10 border-b py-2 text-sm last:border-b-0"
		>
			<span className="badge badge-ghost badge-sm">{`#${row.number}`}</span>
			<a className="link inline-flex items-center gap-1" href={row.url}>
				{row.title}
				{/* (Q-8: the title is a link, and an icon says it goes to GitHub.) */}
				<svg
					aria-hidden="true"
					className="size-3 opacity-60"
					fill="none"
					stroke="currentColor"
					strokeWidth={2}
					viewBox="0 0 24 24"
				>
					<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
				</svg>
			</a>
			{row.task !== "" && <Key kind="tasks" name={row.task} />}
			{row.checks !== "" && row.checks !== "none" && (
				<span className="badge badge-sm">{`checks: ${row.checks}`}</span>
			)}
			{row.review !== "" && row.review !== "none" && (
				<span className="badge badge-sm">{`review: ${row.review}`}</span>
			)}
			{row.merger !== null && <span className="text-xs opacity-70">{row.merger}</span>}
			{row.merged_by !== "" && (
				<span className="text-xs opacity-70">{`merged by ${row.merged_by}`}</span>
			)}
			{row.followed.length > 0 && (
				<span className="text-xs opacity-70">{`followed by ${row.followed.join(" · ")}`}</span>
			)}
			{row.blockers.length > 0 && (
				<span className="text-xs opacity-70">{row.blockers.join(" · ")}</span>
			)}
			{mergeable && row.number > 0 && row.task !== "" && (
				<button
					className="btn btn-xs"
					disabled={busy}
					onClick={() => {
						onMerge(row);
					}}
					type="button"
				>
					{busy ? "Merging…" : "Merge"}
				</button>
			)}
		</li>
	);
}

/**
 * Ready now (numbered in merge order, per repo), coming soon with its blockers, held with its
 * reasons, and what merged lately — and, above them, the project's auto merge said and, for the
 * owner, turned on or off (Q-8). A person may still merge one by hand, which is what the button
 * does: it asks which pull it is, then calls `orch_merge` with the head the row was listed at.
 */
export function Merge({ ctx, merge, project, refresh }: MergeProps) {
	const [merging, setMerging] = useState<string | null>(null);
	const [refused, setRefused] = useState<string | null>(null);
	const [failed, setFailed] = useState<string | null>(null);

	const ask = async (row: MergeRow): Promise<void> => {
		const what = `${row.repo}#${row.number}`;
		const yes = await ctx.confirm(`Merge ${what}?\n\n${row.title}`, {
			ok: "Merge",
			cancel: "Not yet",
		});
		if (!yes) {
			return;
		}
		setMerging(row.task);
		setRefused(null);
		setFailed(null);
		try {
			await ctx.call("orch_merge", { project, key: row.task, confirm: what, sha: row.sha });
			await refresh();
		} catch (failure) {
			// (The refusal is said here, and the row it was about is marked: it is still on the
			// list, and a person reads why.)
			const said = failure instanceof Error ? failure.message : String(failure);
			setFailed(row.task);
			setRefused(said);
		} finally {
			setMerging(null);
		}
	};

	const toggle = async (on: boolean): Promise<void> => {
		setRefused(null);
		try {
			await ctx.call("orch_set_auto_merge", { project, on });
			await refresh();
		} catch (failure) {
			setRefused(failure instanceof Error ? failure.message : String(failure));
		}
	};

	const one = (rows: readonly MergeRow[], mergeable: boolean) =>
		rows.length === 0 ? (
			<p className="text-sm opacity-70">Nothing here.</p>
		) : (
			<ul className="flex flex-col">
				{rows.map((row) => (
					<Row
						busy={merging !== null}
						failed={failed === row.task}
						key={`${row.task}-${row.number}`}
						mergeable={mergeable}
						onMerge={(found) => {
							void ask(found);
						}}
						row={row}
					/>
				))}
			</ul>
		);

	/** A named list under its heading, drawn only when it has something in it. */
	const group = (title: string, rows: readonly MergeRow[], mergeable = false) =>
		rows.length === 0 ? null : (
			<section className="flex flex-col gap-1">
				<h2 className="text-xs font-semibold uppercase tracking-wide opacity-60">{title}</h2>
				{one(rows, mergeable)}
			</section>
		);

	// (The card asks for Ready now per repo: each repository's pulls are their own list.)
	const repos = [...new Set(merge.ready.map((row) => row.repo))];

	return (
		<div className="flex flex-col gap-3">
			<section className="card border border-base-content/20 bg-base-100 shadow-sm">
				<div className="card-body gap-2 p-4">
					<div className="flex flex-wrap items-baseline gap-2">
						<h2 className="text-sm font-semibold">Ready now</h2>
						<span className="text-xs opacity-70">
							{merge.auto_merge && merge.merger !== null
								? `auto merge: ${merge.merger}`
								: "auto merge: off"}
						</span>
						<button
							className="btn btn-xs"
							disabled={merging !== null || (!merge.auto_merge && merge.merger === null)}
							onClick={() => {
								void toggle(!merge.auto_merge);
							}}
							title={
								merge.auto_merge || merge.merger !== null
									? undefined
									: "Name the merger first (29.x's page)"
							}
							type="button"
						>
							{merge.auto_merge ? "Turn off" : "Turn on"}
						</button>
					</div>
					{repos.length === 0 && <p className="text-sm opacity-70">Nothing here.</p>}
					{repos.map((repo) => (
						<div className="flex flex-col gap-1" key={repo}>
							<h3 className="text-xs opacity-60">{repo}</h3>
							{one(
								merge.ready.filter((row) => row.repo === repo),
								true,
							)}
						</div>
					))}
				</div>
			</section>
			{refused !== null && refused !== "" && (
				<p className="alert alert-error alert-soft text-sm" role="alert">
					{refused}
				</p>
			)}
			{group("Coming soon", merge.coming)}
			{group("Held", merge.held)}
			{group("Merged lately", merge.done)}
		</div>
	);
}
