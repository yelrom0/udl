/**
 * --------
 * - page.tsx -
 * --------
 *
 * The two pages 29.8 adds (29.1's screens): Ready to merge, and To action. Both read 28.4's
 * inbox, so both re-read on the same live events.
 */

import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useInbox } from "../hooks";
import { Page, Said } from "../page";
import { Actions } from "./actions";
import { Merge } from "./merge";

/** What Ready to merge is for, in a line (29.1's screen). */
const WHAT = "What is ready to merge, and what only a person can do.";
/** And what To action is for: it has no merge list, so it does not talk about one. */
const ACTIONS = "What only a person can do: now, then after, then later.";

/**
 * Ready to merge (29.8): ready now, numbered in merge order; coming soon with its blockers; held
 * with its reasons; and what merged lately. Above them the project's auto merge switch and its
 * merger (Q-8), and each row's Merge button, which asks which pull it is before it calls
 * `orch_merge` at the head the row was listed at.
 *
 * It reads 28.4's inbox rather than `orch_merge_list` alone, because the inbox carries the merge
 * list, the project, and the to-action list in one answer — and re-reads on the same live
 * events, so a merge lands without a reload.
 */
export const MergePageView: Component<RouteProps> = ({ ctx }) => {
	const { inbox, error, refresh } = useInbox(ctx);

	if (error !== null) {
		return (
			<Page title="Ready to merge">
				<p className="text-error" role="alert">
					{error}
				</p>
			</Page>
		);
	}
	if (inbox === null) {
		return (
			<Page title="Ready to merge">
				<Said>Reading what waits on a person…</Said>
			</Page>
		);
	}

	return (
		<Page title="Ready to merge">
			<Said>{WHAT}</Said>
			<div className="grid gap-4 lg:grid-cols-2">
				<Merge ctx={ctx} merge={inbox.merge} project={inbox.project} refresh={refresh} />
				<Actions actions={inbox.actions} ctx={ctx} project={inbox.project} refresh={refresh} />
			</div>
		</Page>
	);
};

/**
 * To action (29.8): the same answer's action list, each with its result box and Done. It is the
 * inbox's list at full width rather than the strip the inbox shows.
 */
export const ActionsPageView: Component<RouteProps> = ({ ctx }) => {
	const { inbox, error, refresh } = useInbox(ctx);

	if (error !== null) {
		return (
			<Page title="To action">
				<p className="text-error" role="alert">
					{error}
				</p>
			</Page>
		);
	}
	if (inbox === null) {
		return (
			<Page title="To action">
				<Said>Reading what waits on a person…</Said>
			</Page>
		);
	}

	return (
		<Page title="To action">
			<Said>{ACTIONS}</Said>
			<Actions actions={inbox.actions} ctx={ctx} project={inbox.project} refresh={refresh} />
		</Page>
	);
};
