import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useQuestions } from "../hooks";
import { Page, Said } from "../page";
import { Answered, OpenQuestions } from "./questions";
import { useAnswer } from "./useAnswer";

/** What the Q&A page is for, in a line (29.1's screen). */
const WHAT = "What agents are asking, and what was decided: the blocking ones first.";

/**
 * The questions (29.7): every question an agent asked, the blocking ones pinned on top, the rest
 * in one card, and what has been answered under them.
 *
 * It reads the same questions as the inbox (29.3), through a store that watches 28.5's
 * `orch.question.asked` and `orch.question.answered`, so a question asked while the page is open
 * arrives without a reload. Answering is 29.3's handler, shared: `orch_answer`, then a read.
 */
export const QaPage: Component<RouteProps> = ({ ctx }) => {
	const { project, questions, error, refresh } = useQuestions(ctx);
	// (29.3's handler, shared with the inbox: one place decides what answering does. The project
	// is the store's own, from `orch_list_questions`' answer — never the first question's.)
	const { answering, refused, answer } = useAnswer(ctx, project, refresh);

	if (error !== null) {
		return (
			<Page title="Q&A">
				<p className="text-error" role="alert">
					{error}
				</p>
			</Page>
		);
	}
	if (questions === null) {
		return (
			<Page title="Q&A">
				<Said>{WHAT}</Said>
				<Said>Reading what is being asked…</Said>
			</Page>
		);
	}

	const open = questions.filter((one) => one.state === "open");
	const done = questions.filter((one) => one.state !== "open");

	return (
		<Page title="Q&A">
			<Said>{WHAT}</Said>
			<div className="flex flex-wrap items-baseline gap-2">
				<h2 className="text-lg font-semibold">{`${open.length} open`}</h2>
				<span className="text-sm opacity-70">
					{`· ${open.filter((one) => one.blocking).length} blocking · ${done.length} answered`}
				</span>
			</div>
			{refused !== null && (
				<p className="alert alert-error alert-soft text-sm" role="alert">
					{refused}
				</p>
			)}
			<OpenQuestions answer={answer} answering={answering} questions={open} />
			<Answered questions={done} />
		</Page>
	);
};
