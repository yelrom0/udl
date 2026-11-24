import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useInbox } from "../hooks";
import { Page, Said } from "../page";
import { useAnswer } from "../qa/useAnswer";
import { ActionsCard } from "./actions";
import { AgentsStrip } from "./agents";
import { ago } from "./ago";
import { Counts } from "./cards";
import { MergeCard } from "./merge";
import { useNow } from "./now";
import { Pinned, Questions } from "./questions";

/** What the inbox is for, in a line (29.1's screen). */
const WHAT = "What needs you: questions, PRs ready to merge, and things only a person can do.";

/**
 * The inbox (29.3): the home route, and the one page a person reads first.
 *
 * The blocking questions are pinned on top, each with its options as one click; the three counts
 * (28.4's three lists) say what is behind them; the agents strip says what every agent is on;
 * and the merge, questions and to-action cards hold the rest. Answering calls `orch_answer`, then
 * reads the inbox again, so the question leaves and its button comes back at once — without
 * waiting for `orch.question.answered` (28.5) to arrive.
 */
export const InboxPage: Component<RouteProps> = ({ ctx }) => {
	const { inbox, error, refresh } = useInbox(ctx);
	const now = useNow();
	// (29.3's handler, shared with 29.7's page: one place decides what answering does. It is
	// called before the early returns below — a hook is called every draw or none.)
	const { answering, refused, answer } = useAnswer(ctx, inbox?.project ?? "", refresh);

	if (error !== null) {
		return (
			<Page title="Inbox">
				<p className="text-error" role="alert">
					{error}
				</p>
			</Page>
		);
	}
	if (inbox === null) {
		return (
			<Page title="Inbox">
				<Said>Reading what waits on a person…</Said>
			</Page>
		);
	}

	const blocking = inbox.questions.filter((one) => one.blocking);
	const rest = inbox.questions.filter((one) => !one.blocking);
	const actions = inbox.actions;
	return (
		<Page title="Inbox">
			<Said>{WHAT}</Said>
			{blocking.map((question) => (
				<Pinned answer={answer} answering={answering} key={question.key} question={question} />
			))}
			<Counts
				after={actions.filter((one) => one.when === "after").length}
				blocking={blocking.length}
				coming={inbox.merge.coming.length}
				held={inbox.merge.held.length}
				later={actions.filter((one) => one.when === "later").length}
				now={actions.filter((one) => one.when === "now").length}
				questions={inbox.questions.length}
				ready={inbox.merge.ready.length}
			/>
			<AgentsStrip agents={inbox.agents} />
			{refused !== null && (
				<p className="alert alert-error alert-soft text-sm" role="alert">
					{refused}
				</p>
			)}
			<div className="grid gap-4 lg:grid-cols-2">
				<MergeCard merge={inbox.merge} />
				<div className="flex flex-col gap-4">
					<Questions answer={answer} answering={answering} questions={rest} />
					<ActionsCard actions={actions} />
				</div>
			</div>
			<footer className="text-xs opacity-60">{`Updated ${ago(inbox.updated_at, now)}`}</footer>
		</Page>
	);
};
