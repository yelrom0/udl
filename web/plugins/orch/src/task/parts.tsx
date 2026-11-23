import { useState } from "react";
import { KeyLink } from "../keyLink";
import { LinkChip } from "../linkChip";
import { type Link, movesFrom, type Readiness, type TaskSession } from "../model";

/** The keys of a list, each a link to its own page, or a line saying there are none. */
export function Keys({
	name,
	keys,
	kind,
	empty,
}: {
	readonly name: string;
	readonly keys: readonly string[];
	readonly kind: "task" | "feature" | "question";
	readonly empty: string;
}) {
	return (
		<section className="flex flex-col gap-1">
			<h2 className="text-sm font-semibold">{name}</h2>
			{keys.length === 0 ? (
				<p className="text-sm opacity-70">{empty}</p>
			) : (
				<ul aria-label={name} className="flex flex-wrap gap-2">
					{keys.map((key) => (
						<li key={key}>
							<KeyLink kind={kind} label={key} />
						</li>
					))}
				</ul>
			)}
		</section>
	);
}

/**
 * A task's state, and the moves a person may make (29.5): every state but the one it is in — a
 * person moves a task anywhere, and the state table's rules are the agents'.
 */
export function StateBox({
	state,
	onMove,
}: {
	readonly state: string;
	readonly onMove: (to: string) => Promise<void>;
}) {
	const moves = movesFrom(state);
	const [to, setTo] = useState(moves[0] ?? "");
	const [busy, setBusy] = useState(false);
	return (
		<section aria-label="State" className="flex flex-col gap-2">
			<div className="flex items-center gap-2">
				<span className="badge badge-ghost border-base-content/20" data-state={state}>
					{state}
				</span>
			</div>
			<div className="flex items-end gap-2">
				<label className="flex flex-col gap-1 text-xs">
					Move to
					<select
						aria-label="Move to"
						className="select select-xs select-bordered"
						onChange={(event) => {
							setTo(event.target.value);
						}}
						value={to}
					>
						{moves.map((one) => (
							<option key={one} value={one}>
								{one}
							</option>
						))}
					</select>
				</label>
				<button
					className="btn btn-xs btn-outline"
					disabled={busy || to === ""}
					onClick={() => {
						setBusy(true);
						void onMove(to).finally(() => {
							setBusy(false);
						});
					}}
					type="button"
				>
					Move
				</button>
			</div>
		</section>
	);
}

/** The task's pull, and whether udl says it may be merged (27.6): the blockers, and the bot's
 * items it still lists. */
export function PullCard({
	links,
	readiness,
}: {
	readonly links: readonly Link[];
	readonly readiness: Readiness | null;
}) {
	const pulls = links.filter((link) => link.link_kind === "pull");
	return (
		<section
			aria-label="Pull"
			className="flex flex-col gap-2 rounded-lg border border-base-content/20 p-3 shadow-sm"
		>
			<h2 className="text-sm font-semibold">Pull</h2>
			{pulls.length === 0 ? (
				<p className="text-sm opacity-70">No pull yet.</p>
			) : (
				<ul className="flex flex-wrap gap-2">
					{pulls.map((link) => (
						<li key={link.url}>
							<LinkChip
								kind={link.link_kind}
								state={link.state}
								title={link.title}
								url={link.url}
							/>
						</li>
					))}
				</ul>
			)}
			{pulls.length > 0 && readiness === null && (
				<p className="text-xs opacity-70">Whether it may be merged is not known right now.</p>
			)}
			{readiness !== null && pulls.length > 0 && (
				<>
					<p className="text-sm font-semibold">
						{readiness.ready ? "Ready to merge" : "Not ready"}
					</p>
					{readiness.blockers.length > 0 && (
						<ul aria-label="Blockers" className="list-inside list-disc text-xs">
							{readiness.blockers.map((blocker) => (
								<li key={blocker}>{blocker}</li>
							))}
						</ul>
					)}
					{readiness.open_items.length > 0 && (
						<div className="flex flex-col gap-1">
							<p className="text-xs font-semibold">The bot still lists</p>
							<ul aria-label="Open items" className="list-inside list-disc text-xs">
								{readiness.open_items.map((item) => (
									<li key={item}>{item}</li>
								))}
							</ul>
						</div>
					)}
					{readiness.held && <p className="text-xs opacity-70">Held: built now, merged later.</p>}
				</>
			)}
		</section>
	);
}

/** Where the task is being worked: the agent's page, at that session. */
export function SessionLine({ session }: { readonly session: TaskSession | null }) {
	return (
		<section className="flex flex-col gap-1">
			<h2 className="text-sm font-semibold">Session</h2>
			{session === null ? (
				<p className="text-sm opacity-70">No session right now.</p>
			) : (
				<a
					className="link link-hover text-sm"
					href={`/ui/p/orch/agents/${encodeURIComponent(session.agent)}?session=${encodeURIComponent(session.id)}`}
				>
					{session.agent}'s session, {session.state}
				</a>
			)}
		</section>
	);
}
