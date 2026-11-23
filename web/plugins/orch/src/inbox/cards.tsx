import type { ReactNode } from "react";
import { LinkChip } from "../linkChip";

/** A card with a clear edge (DESIGN.md's rule: a base-content 20 % border and a shadow). */
const CARD = "card border border-base-content/20 bg-base-100 shadow-sm";

/**
 * One of the inbox's cards: its title, how many things are in it, and what is in it. Named, so
 * a reader (or a test) can tell one card from another.
 */
export function Card({
	title,
	count,
	aside,
	children,
}: {
	readonly title: string;
	readonly count?: number | undefined;
	readonly aside?: ReactNode;
	readonly children: ReactNode;
}) {
	return (
		<section aria-label={title} className={CARD}>
			<div className="card-body gap-3 p-4">
				<header className="flex flex-wrap items-center gap-2">
					<h2 className="card-title text-base">{title}</h2>
					{count !== undefined && <span className="badge badge-ghost badge-sm">{count}</span>}
					{aside !== undefined && <div className="ml-auto">{aside}</div>}
				</header>
				{children}
			</div>
		</section>
	);
}

/** One of the three numbers at the top: what it counts, the number, and what it is made of. */
function Count({
	label,
	value,
	note,
}: {
	readonly label: string;
	readonly value: number;
	readonly note: string;
}) {
	return (
		<fieldset className={`${CARD} p-4`}>
			<legend className="px-1 text-sm opacity-70">{label}</legend>
			<p className="text-3xl font-semibold">{value}</p>
			<p className="text-xs opacity-70">{note}</p>
		</fieldset>
	);
}

/** "1 question", "2 questions": a count and its word. */
function counted(count: number, one: string, many: string): string {
	return count === 1 ? `1 ${one}` : `${count} ${many}`;
}

/**
 * The three counts the inbox is (28.4), side by side: what is ready to merge (and what is coming
 * or held behind it), what is being asked (and how much of it blocks), and what only a person
 * can do (and when).
 */
export function Counts({
	ready,
	coming,
	held,
	questions,
	blocking,
	now,
	after,
	later,
}: {
	readonly ready: number;
	readonly coming: number;
	readonly held: number;
	readonly questions: number;
	readonly blocking: number;
	readonly now: number;
	readonly after: number;
	readonly later: number;
}) {
	return (
		<div className="grid gap-4 sm:grid-cols-3">
			<Count label="Ready to merge" value={ready} note={`${coming} coming soon · ${held} held`} />
			<Count
				label="Open questions"
				value={questions}
				note={blocking === 0 ? "none blocking" : `${counted(blocking, "blocking", "blocking")}`}
			/>
			<Count
				label="To action"
				value={now + after + later}
				note={`${now} now · ${after} after · ${later} later`}
			/>
		</div>
	);
}

/** A task's, a feature's or a question's key: always a link to its own page (DESIGN.md). */
export function KeyLink({
	kind,
	keyName,
}: {
	readonly kind: "tasks" | "features" | "questions";
	readonly keyName: string;
}) {
	return (
		<a
			className="font-mono text-sm link link-hover"
			href={`/ui/p/orch/${kind}/${encodeURIComponent(keyName)}`}
		>
			{keyName}
		</a>
	);
}

/** A pull, as the merge lists draw it: its chip, which is a real link to it (Q-8). */
export function PullLink({ url, title }: { readonly url: string; readonly title: string }) {
	return <LinkChip kind="pull" url={url} title={title} />;
}
