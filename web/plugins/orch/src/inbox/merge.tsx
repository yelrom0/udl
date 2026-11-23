import type { MergeList, MergeRow } from "../model";
import { Card, KeyLink, PullLink } from "./cards";

/** One pull in a list: its chip (a real link, Q-8), its title, its task, and why it waits. */
function Row({ row, why }: { readonly row: MergeRow; readonly why?: string }) {
	return (
		<li className="flex flex-wrap items-center gap-2 border-base-content/10 border-b py-2 last:border-b-0">
			<PullLink title={row.title} url={row.url} />
			<span className="min-w-0 flex-1 truncate text-sm">{row.title}</span>
			{row.task !== "" && <KeyLink kind="tasks" keyName={row.task} />}
			{why !== undefined && why !== "" && <span className="text-xs opacity-70">{why}</span>}
		</li>
	);
}

/** What a merge list says it waits on, in a line: its blockers, or its place in a stack. */
function waiting(row: MergeRow): string {
	return row.blockers.join(" · ");
}

/** A list of pulls with its own heading, or nothing when it is empty. */
function List({
	title,
	rows,
	said,
}: {
	readonly title: string;
	readonly rows: readonly MergeRow[];
	readonly said?: string;
}) {
	if (rows.length === 0) {
		return null;
	}
	return (
		<div className="flex flex-col gap-1">
			<h3 className="text-xs font-semibold uppercase tracking-wide opacity-60">{title}</h3>
			<ul className="flex flex-col">
				{rows.map((row) => (
					<Row key={`${row.task}-${row.number}`} row={row} why={said ?? waiting(row)} />
				))}
			</ul>
		</div>
	);
}

/**
 * What is ready to merge, what is coming and what is held (28.3), in one card — with the
 * project's auto merge said at its top (Q-8): whether it is on, and whose merger does it. Every
 * pull is a real link to it.
 */
export function MergeCard({ merge }: { readonly merge: MergeList }) {
	const ready = merge.ready.length;
	return (
		<Card
			aside={
				<span className="text-xs opacity-70">
					{merge.auto_merge && merge.merger !== null
						? `auto merge: ${merge.merger}`
						: "auto merge: off"}
				</span>
			}
			count={ready}
			title="Ready to merge"
		>
			{ready === 0 ? (
				<p className="text-sm opacity-70">Nothing is ready to merge.</p>
			) : (
				<ul className="flex flex-col">
					{merge.ready.map((row) => (
						<Row key={`${row.task}-${row.number}`} row={row} />
					))}
				</ul>
			)}
			<List rows={merge.coming} title="Coming soon" />
			<List rows={merge.held} title="Held" />
		</Card>
	);
}
