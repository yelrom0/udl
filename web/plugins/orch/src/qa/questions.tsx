import { Card, KeyLink } from "../inbox/cards";
import { Pinned, type QuestionProps, QuestionRow, threadOf } from "../inbox/questions";
import type { Question } from "../model";

/**
 * One answered question, in the answered card: what it asked, what was decided, and by whom.
 * The thread has the rest (29.7's screen: "the answered ones with their outcomes").
 */
export function AnsweredRow({ question }: { readonly question: Question }) {
	const outcome = question.outcome ?? question.answer ?? "";
	return (
		<li className="flex flex-col gap-1 border-base-content/10 border-b py-2 last:border-b-0">
			<div className="flex flex-wrap items-center gap-2 text-sm">
				<KeyLink kind="questions" keyName={question.key} />
				<span className="badge badge-ghost badge-sm">{question.state}</span>
				{question.task !== null && <KeyLink kind="tasks" keyName={question.task} />}
			</div>
			<a className="font-medium link link-hover" href={threadOf(question.key)}>
				{question.title}
			</a>
			{outcome !== "" && (
				<p className="text-sm opacity-80">
					{question.answered_by !== null && question.answered_by !== ""
						? `${question.answered_by}: `
						: ""}
					{outcome}
				</p>
			)}
		</li>
	);
}

/** Every answered or closed question, in one card: what was decided, in a line each. */
export function Answered({ questions }: { readonly questions: readonly Question[] }) {
	return (
		<Card count={questions.length} title="Answered">
			{questions.length === 0 ? (
				<p className="text-sm opacity-70">Nothing has been answered yet.</p>
			) : (
				<ul className="flex flex-col">
					{questions.map((question) => (
						<AnsweredRow key={question.key} question={question} />
					))}
				</ul>
			)}
		</Card>
	);
}

/**
 * The open questions (29.7): the blocking ones pinned first, then the rest in one card, then
 * what has been answered. The question components are 29.3's (the inbox draws the same ones), so
    a question is drawn once in the plugin.
 */
export function OpenQuestions({
	questions,
	answering,
	answer,
}: {
	readonly questions: readonly Question[];
	readonly answering: ReadonlySet<string>;
	answer: QuestionProps["answer"];
}) {
	const blocking = questions.filter((one) => one.blocking);
	const rest = questions.filter((one) => !one.blocking);
	return (
		<>
			{blocking.map((question) => (
				<Pinned answer={answer} answering={answering} key={question.key} question={question} />
			))}
			<Card count={rest.length} title="Being asked">
				{rest.length === 0 ? (
					<p className="text-sm opacity-70">
						{blocking.length === 0 ? "Nothing is being asked." : "Nothing else is waiting."}
					</p>
				) : (
					<ul className="flex flex-col">
						{rest.map((question) => (
							<QuestionRow
								answer={answer}
								answering={answering}
								key={question.key}
								question={question}
							/>
						))}
					</ul>
				)}
			</Card>
		</>
	);
}
