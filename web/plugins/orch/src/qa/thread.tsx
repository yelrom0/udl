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

/**
 * A question's own thread (29.7): what it asks, what it is about, what has been said, and what
 * it was answered with — with the answer, the follow-ups and the outcome in one place.
 */
export function QuestionThreadView({ ctx, found, project, refresh }: ThreadProps) {
	const { question, thread, tasks, features } = found;
	const { answering, refused, answer } = useAnswer(ctx, project, refresh);
	const [typing, setTyping] = useState<string | null>(null);
	const [typed, setTyped] = useState<string | null>(null);
	const [replyFailed, setReplyFailed] = useState<string | null>(null);
	const [saying, setSaying] = useState(false);
	const [outcome, setOutcome] = useState<string | null>(null);
	const [closeFailed, setCloseFailed] = useState<string | null>(null);
	const [closing, setClosing] = useState(false);

	const open = question.state === "open";
	const busy = answering.has(question.key);

	/** A write of the page's: whether it worked, and a refusal is said, not swallowed. */
	const write = async (
		name: string,
		body: Record<string, string>,
		failed: (said: string) => void,
	): Promise<boolean> => {
		try {
			await ctx.call(name, { project, key: question.key, ...body });
			failed("");
			await refresh();
			return true;
		} catch (failure) {
			failed(messageOf(failure));
			return false;
		}
	};

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

			<About features={features} task={question.task} tasks={tasks} />

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

			{open && (
				<>
					{question.options.length > 0 ? (
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
						</div>
					) : (
						// (A question that offers no options is answered in its own words: the same
						// `orch_answer`, with what was typed.)
						<form
							className="flex flex-col gap-2"
							onSubmit={(event) => {
								event.preventDefault();
								const said = typed ?? "";
								if (said.trim() === "" || busy) {
									return;
								}
								answer(question, said.trim());
							}}
						>
							<label className="text-sm">
								Answer
								<textarea
									className="textarea textarea-bordered w-full"
									rows={3}
									value={typed ?? ""}
									onChange={(event) => {
										setTyped(event.target.value);
									}}
								/>
							</label>
							<div className="flex items-center gap-2">
								<button className="btn btn-sm" disabled={busy} type="submit">
									Answer
								</button>
								{busy && <span className="text-xs opacity-70">Answering…</span>}
							</div>
						</form>
					)}
				</>
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
							const said = typing ?? "";
							if (said.trim() === "") {
								return;
							}
							setSaying(true);
							void (async () => {
								if (await write("orch_reply", { text: said }, setReplyFailed)) {
									setTyping(null);
								}
								setSaying(false);
							})();
						}}
					>
						<label className="text-sm">
							Follow up
							<textarea
								className="textarea textarea-bordered w-full"
								rows={3}
								value={typing ?? ""}
								onChange={(event) => {
									setTyping(event.target.value);
								}}
							/>
						</label>
						{replyFailed !== null && replyFailed !== "" && (
							<p className="text-sm text-error" role="alert">
								{replyFailed}
							</p>
						)}
						<button className="btn btn-sm" disabled={saying} type="submit">
							{saying ? "Saying…" : "Say it"}
						</button>
					</form>

					<form
						className="flex flex-col gap-2"
						onSubmit={(event) => {
							event.preventDefault();
							const said = outcome ?? "";
							if (said.trim() === "") {
								return;
							}
							setClosing(true);
							void (async () => {
								if (await write("orch_close_question", { outcome: said }, setCloseFailed)) {
									setOutcome(null);
								}
								setClosing(false);
							})();
						}}
					>
						<label className="text-sm">
							Close with outcome
							<input
								className="input input-bordered w-full"
								value={outcome ?? ""}
								onChange={(event) => {
									setOutcome(event.target.value);
								}}
							/>
						</label>
						{closeFailed !== null && closeFailed !== "" && (
							<p className="text-sm text-error" role="alert">
								{closeFailed}
							</p>
						)}
						<button className="btn btn-sm btn-outline" disabled={closing} type="submit">
							{closing ? "Closing…" : "Close"}
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

/** What a question is about: the task it was asked in, the other tasks it names, and the
 * features it is about — each a link to its own page. */
function About({
	tasks,
	features,
	task,
}: {
	readonly tasks: readonly string[];
	readonly features: readonly string[];
	readonly task: string | null;
}) {
	if (task === null && tasks.length === 0 && features.length === 0) {
		return null;
	}
	const others = tasks.filter((one) => one !== task);
	return (
		<div className="flex flex-wrap items-center gap-2 text-xs opacity-70">
			<span>about</span>
			{task !== null && <KeyLink kind="tasks" keyName={task} />}
			{others.map((one) => (
				<KeyLink key={`t-${one}`} kind="tasks" keyName={one} />
			))}
			{features.map((one) => (
				<KeyLink key={`f-${one}`} kind="features" keyName={one} />
			))}
		</div>
	);
}
