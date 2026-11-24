import type { UIContext } from "@udl/ui-sdk";
import { useState } from "react";
import { Card, KeyLink } from "../inbox/cards";
import { messageOf } from "../json";
import { Markdown } from "../markdown";
import type { QuestionThread } from "../model";
import { useAnswer } from "./useAnswer";

/** What the thread is given: the question with its thread, and what the page does with it. */
export interface ThreadProps {
	readonly ctx: UIContext;
	readonly found: QuestionThread;
	readonly project: string;
	/** Read it again: what the page asks after it wrote something (a reply, a close). */
	refresh(): Promise<void>;
}

/** A question's own thread (29.7): what it asks, what it is about, what has been said, and what
 * it was answered with — with the answer, the follow-ups and the outcome in one place. */
export function QuestionThreadView({ ctx, found, project, refresh }: ThreadProps) {
	const { question, thread, tasks, features } = found;
	const { answering, refused, answer } = useAnswer(ctx, project, refresh);
	const [saying, setSaying] = useState<string | null>(null);
	const [said, setSaid] = useState<string | null>(null);
	const [closing, setClosing] = useState<string | null>(null);

	const open = question.state === "open";

	return (
		<article className="flex flex-col gap-4">
			<header className="flex flex-col gap-2">
				<div className="flex flex-wrap items-center gap-2 text-sm">
					<KeyLink kind="questions" keyName={question.key} />
					<span className="badge badge-ghost badge-sm">{question.state}</span>
					{question.blocking && <span className="badge badge-error badge-sm">BLOCKING</span>}
					{question.escalated && <span className="badge badge-warning badge-sm">ESCALATED</span>}
				</div>
				<h2 className="text-xl font-semibold">{question.title}</h2>
				<p className="text-xs opacity-70">
					{`asked by ${question.asked_by === "" ? "udl" : question.asked_by}${
						question.to === "" ? "" : ` · to ${question.to}`
					}`}
				</p>
			</header>

			{question.text !== "" && <Markdown text={question.text} />}

			<About tasks={tasks} features={features} task={question.task} />

			{question.answer !== null && question.answer !== "" && (
				<section
					aria-label="Answer"
					className="card border border-base-content/20 border-l-4 border-l-success bg-base-100 shadow-sm"
				>
					<div className="card-body gap-2 p-4">
						<h3 className="text-sm font-semibold">
							{question.answered_by !== null && question.answered_by !== ""
								? `Answered by ${question.answered_by}`
								: "Answered"}
						</h3>
						<Markdown text={question.answer} />
					</div>
				</section>
			)}

			{question.outcome !== null && question.outcome !== "" && (
				<p className="text-sm opacity-80">{`Closed: ${question.outcome}`}</p>
			)}

			{question.options.length > 0 && open && (
				<div className="flex flex-wrap items-center gap-2">
					{question.options.map((option) => (
						<button
							className="btn btn-sm btn-outline"
							disabled={answering.has(question.key)}
							key={option}
							onClick={() => {
								answer(question, option);
							}}
							type="button"
						>
							{option}
						</button>
					))}
					{answering.has(question.key) && <span className="text-xs opacity-70">Answering…</span>}
				</div>
			)}

			{refused !== null && (
				<p className="alert alert-error alert-soft text-sm" role="alert">
					{refused}
				</p>
			)}

			<Card count={thread.length} title="Thread">
				{thread.length === 0 ? (
					<p className="text-sm opacity-70">Nothing has been said yet.</p>
				) : (
					<ul className="flex flex-col gap-3">
						{thread.map((one, index) => (
							<li className="flex flex-col gap-1" key={`${one.at}-${index}`}>
								<span className="text-xs opacity-70">{one.author}</span>
								<Markdown text={one.text} />
							</li>
						))}
					</ul>
				)}
			</Card>

			{open && (
				<>
					<form
						className="flex flex-col gap-2"
						onSubmit={(event) => {
							event.preventDefault();
							const text = saying ?? "";
							if (text.trim() === "") {
								return;
							}
							setSaying(null);
							void (async () => {
								setSaid(null);
								try {
									await ctx.call("orch_reply", {
										project,
										key: question.key,
										text,
									});
								} catch (failure) {
									setSaid(messageOf(failure));
								}
								await refresh();
							})();
						}}
					>
						<label className="text-sm">
							Follow up
							<textarea
								className="textarea textarea-bordered w-full"
								rows={3}
								value={saying ?? ""}
								onChange={(event) => {
									setSaying(event.target.value);
								}}
							/>
						</label>
						{said !== null && (
							<p className="text-sm text-error" role="alert">
								{said}
							</p>
						)}
						<button className="btn btn-sm" type="submit">
							Say it
						</button>
					</form>

					<form
						className="flex flex-col gap-2"
						onSubmit={(event) => {
							event.preventDefault();
							const outcome = closing ?? "";
							if (outcome.trim() === "") {
								return;
							}
							setClosing(null);
							void (async () => {
								setSaid(null);
								try {
									await ctx.call("orch_close_question", {
										project,
										key: question.key,
										outcome,
									});
								} catch (failure) {
									setSaid(messageOf(failure));
								}
								await refresh();
							})();
						}}
					>
						<label className="text-sm">
							Close with outcome
							<input
								className="input input-bordered w-full"
								value={closing ?? ""}
								onChange={(event) => {
									setClosing(event.target.value);
								}}
							/>
						</label>
						<button className="btn btn-sm btn-outline" type="submit">
							Close
						</button>
					</form>
				</>
			)}

			<footer className="text-xs opacity-60">
				<a className="link" href="/ui/p/orch/questions">
					All questions
				</a>
			</footer>
		</article>
	);
}

/** What a question is about: the tasks and features it names, and the task it was asked in. */
function About({
	tasks,
	features,
	task,
}: {
	readonly tasks: readonly string[];
	readonly features: readonly string[];
	readonly task: string | null;
}) {
	const anything = task !== null || tasks.length > 0 || features.length > 0;
	if (!anything) {
		return null;
	}
	return (
		<div className="flex flex-wrap items-center gap-2 text-xs opacity-70">
			<span>about</span>
			{tasks.includes(task ?? "") && task !== null && <KeyLink kind="tasks" keyName={task} />}
			{tasks
				.filter((one) => one !== task)
				.map((one) => (
					<KeyLink key={`t-${one}`} kind="tasks" keyName={one} />
				))}
			{features.map((one) => (
				<KeyLink key={`f-${one}`} kind="features" keyName={one} />
			))}
		</div>
	);
}
