import type { Question } from "../model";
import { Card, KeyLink } from "./cards";

/** What a question's card is given: the question, and what answering it does. */
export interface QuestionProps {
	readonly question: Question;
	/** The question being answered, when one is (so its buttons say so and take no click). */
	readonly answering: string | null;
	answer(question: Question, choice: string): void;
}

/** Where a question's own thread is. */
export function threadOf(key: string): string {
	return `/ui/p/orch/questions/${encodeURIComponent(key)}`;
}

/** What a question is, in its own line: its key, and whether it blocks. */
function Head({ question }: { readonly question: Question }) {
	return (
		<div className="flex flex-wrap items-center gap-2 text-sm">
			<KeyLink kind="questions" keyName={question.key} />
			{question.blocking && <span className="badge badge-error badge-sm">BLOCKING</span>}
		</div>
	);
}

/** Who asked it, and what it is about. */
function About({ question }: { readonly question: Question }) {
	if (question.asked_by === "" && question.task === null) {
		return null;
	}
	return (
		<div className="flex flex-wrap items-center gap-2 text-xs opacity-70">
			{question.asked_by !== "" && <span>{`asked by ${question.asked_by}`}</span>}
			{question.task !== null && <KeyLink kind="tasks" keyName={question.task} />}
		</div>
	);
}

/** The buttons a question offers: one click per option, or the thread when it offers none. */
function Answers({ question, answering, answer }: QuestionProps) {
	const busy = answering === question.key;
	if (question.options.length === 0) {
		return (
			<a className="link text-sm" href={threadOf(question.key)}>
				Open thread
			</a>
		);
	}
	return (
		<div className="flex flex-wrap items-center gap-2">
			{question.options.map((option) => (
				<button
					className="btn btn-sm btn-outline"
					disabled={busy}
					key={option}
					onClick={() => {
						answer(question, option);
					}}
					type="button"
				>
					{option}
				</button>
			))}
			{busy && <span className="text-xs opacity-70">Answering…</span>}
			<a className="btn btn-ghost btn-sm" href={threadOf(question.key)}>
				Open thread
			</a>
		</div>
	);
}

/**
 * A blocking question, pinned on top of the inbox (29.3): what it asks, who asked it, what it is
 * about, and its options as one-click answers.
 */
export function Pinned({ question, answering, answer }: QuestionProps) {
	return (
		<section
			aria-label="Blocking"
			className="card border border-base-content/20 border-l-4 border-l-error bg-base-100 shadow-sm"
		>
			<div className="card-body gap-3 p-4">
				<Head question={question} />
				<a className="text-lg font-semibold link link-hover" href={threadOf(question.key)}>
					{question.title}
				</a>
				{question.text !== "" && <p className="text-sm opacity-80">{question.text}</p>}
				<About question={question} />
				<Answers answer={answer} answering={answering} question={question} />
			</div>
		</section>
	);
}

/** One open question, in the questions card: what it asks, who asked it, and how to answer. */
export function QuestionRow({ question, answering, answer }: QuestionProps) {
	return (
		<li className="flex flex-col gap-1 border-base-content/10 border-b py-2 last:border-b-0">
			<Head question={question} />
			<a className="font-medium link link-hover" href={threadOf(question.key)}>
				{question.title}
			</a>
			<About question={question} />
			{question.options.length > 0 && (
				<Answers answer={answer} answering={answering} question={question} />
			)}
		</li>
	);
}

/** Every open question, in one card: the blocking ones were pinned above. */
export function Questions({
	questions,
	answering,
	answer,
}: {
	readonly questions: readonly Question[];
	readonly answering: string | null;
	answer(question: Question, choice: string): void;
}) {
	return (
		<Card
			aside={
				<a className="link text-sm" href="/ui/p/orch/questions">
					View all
				</a>
			}
			count={questions.length}
			title="Questions"
		>
			{questions.length === 0 ? (
				<p className="text-sm opacity-70">Nothing is being asked.</p>
			) : (
				<ul className="flex flex-col">
					{questions.map((question) => (
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
	);
}
