import type { Action } from "../model";
import { Card, KeyLink } from "./cards";

/** The three groups an action can be in (28.2), in the order a person reads them. */
const GROUPS: readonly { readonly when: string; readonly title: string }[] = [
	{ when: "now", title: "Now" },
	{ when: "after", title: "After" },
	{ when: "later", title: "Later" },
];

/** One thing only a person can do: its key, what it is, and what it waits on. */
function Row({ action }: { readonly action: Action }) {
	return (
		<li className="flex flex-wrap items-center gap-2 border-base-content/10 border-b py-2 text-sm last:border-b-0">
			<KeyLink kind="tasks" keyName={action.key} />
			<span className="min-w-0 flex-1">{action.title}</span>
			{action.after !== null && (
				<span className="flex items-center gap-1 text-xs opacity-70">
					<span>after</span>
					<KeyLink kind="tasks" keyName={action.after} />
				</span>
			)}
			{action.task !== null && <KeyLink kind="tasks" keyName={action.task} />}
		</li>
	);
}

/**
 * What only a person can do (28.2): now, then after, then later — each with what it is and what
 * it waits on. 29.8 gives each its result box and its Done; here they are what the inbox says.
 */
export function ActionsCard({ actions }: { readonly actions: readonly Action[] }) {
	return (
		<Card count={actions.length} title="To action">
			{actions.length === 0 ? (
				<p className="text-sm opacity-70">Nothing needs a person.</p>
			) : (
				GROUPS.map((group) => {
					const mine = actions.filter((one) => one.when === group.when);
					if (mine.length === 0) {
						return null;
					}
					return (
						<div className="flex flex-col gap-1" key={group.when}>
							<h3 className="text-xs font-semibold uppercase tracking-wide opacity-60">
								{group.title}
							</h3>
							<ul className="flex flex-col">
								{mine.map((action) => (
									<Row action={action} key={action.key} />
								))}
							</ul>
						</div>
					);
				})
			)}
		</Card>
	);
}
