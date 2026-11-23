import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useState } from "react";
import { useInbox } from "../hooks";
import { messageOf } from "../json";
import type { Question } from "../model";
import { Page, Said } from "../page";
import { ActionsCard } from "./actions";
import { AgentsStrip } from "./agents";
import { ago } from "./ago";
import { Counts } from "./cards";
import { MergeCard } from "./merge";
import { Pinned, Questions } from "./questions";

/** What the inbox is for, in a line (29.1's screen). */
const WHAT = "What needs you: questions, PRs ready to merge, and things only a person can do.";

/**
 * The inbox (29.3): the home route, and the one page a person reads first.
 *
 * The blocking questions are pinned on top, each with its options as one click; the three counts
 * (28.4's three lists) say what is behind them; the agents strip says what every agent is on;
 * and the merge, questions and to-action cards hold the rest. Answering calls `orch_answer`, then
 * reads the inbox again — a question and a to-action have no event of their own yet (28.5 gives
 * them one), so the page is what says the answer landed.
 */
export const InboxPage: Component<RouteProps> = ({ ctx }) => {
	const { inbox, error, refresh } = useInbox(ctx);
	const [answering, setAnswering] = useState<string | null>(null);
	const [refused, setRefused] = useState<string | null>(null);

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

	const project = inbox.project;
	const answer = (question: Question, choice: string): void => {
		setAnswering(question.key);
		setRefused(null);
		void (async () => {
			try {
				await ctx.call("orch_answer", {
					project,
					key: question.key,
					answer: choice,
				});
			} catch (failure) {
				// (A refusal is udl's, and it says why: the question stays, and the reason does too.)
				setRefused(messageOf(failure));
			} finally {
				setAnswering(null);
				await refresh();
			}
		})();
	};

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
			<footer className="text-xs opacity-60">{`Updated ${ago(inbox.updated_at)}`}</footer>
		</Page>
	);
};
