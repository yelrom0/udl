import { useRef, useState } from "react";
import { COMMANDS, completed, completing, matching } from "./keys";

/** What the composer is given: what the chat is doing, and what a person's words do. */
export interface ComposerProps {
	/** Whether a turn is running (so End chat is offered beside Send). */
	readonly busy: boolean;
	/** Whether the chat is over (it takes no more turns). */
	readonly over: boolean;
	/** How many things said are queued for the turns after this one. */
	readonly pending: number;
	/** The keys `@` completes: the project's tasks, features and questions. */
	readonly keys: readonly string[];
	onSend(text: string): Promise<void>;
	onStop(): Promise<void>;
}

/**
 * The chat's composer (29.12): what a person says to their harness. Enter sends and Shift+Enter
 * is a new line (as every chat is); `@` completes a task, a feature or a question, and `/` the
 * harness's own commands.
 *
 * A chat takes one turn at a time, so what is said while one runs is queued for the next one (the
 * count beside Send says how many); the turn is not interrupted for it. Ending the chat is a
 * different thing — its harness stops and the conversation takes no more turns — so that button
 * says what it does, and the page asks before it does it.
 */
export function Composer({ busy, over, pending, keys, onSend, onStop }: ComposerProps) {
	const [text, setText] = useState("");
	const [caret, setCaret] = useState(0);
	const [picking, setPicking] = useState(false);
	const box = useRef<HTMLTextAreaElement | null>(null);

	const found = completing(text, caret);
	const options = found === null ? [] : matching(found.token, found.kind === "/" ? COMMANDS : keys);
	const showing = !over && found !== null && options.length > 0 && picking;

	const insert = (choice: string): void => {
		// (The key goes in with the `@` it was typed with; a command is its own whole word.)
		const done = completed(text, caret, found?.kind === "@" ? `@${choice}` : choice);
		setText(done.text);
		setCaret(done.caret);
		setPicking(false);
		// (The caret goes where the key ends, so typing carries on from there.)
		queueMicrotask(() => {
			box.current?.focus();
			box.current?.setSelectionRange(done.caret, done.caret);
		});
	};

	const send = async (): Promise<void> => {
		const said = text.trim();
		if (said === "" || over) {
			return;
		}
		setText("");
		setCaret(0);
		setPicking(false);
		await onSend(said);
	};

	return (
		<div className="border-t border-base-content/20 p-2">
			{showing && (
				<ul
					className="mb-1 max-h-40 overflow-y-auto rounded border border-base-content/20 bg-base-100 text-sm shadow"
					aria-label="Completions"
				>
					{options.slice(0, 20).map((option) => (
						<li key={option}>
							<button
								type="button"
								className="w-full px-2 py-1 text-left hover:bg-base-200"
								onClick={() => {
									insert(option);
								}}
							>
								{option}
							</button>
						</li>
					))}
				</ul>
			)}
			<textarea
				ref={box}
				className="textarea textarea-bordered h-20 w-full resize-none text-sm"
				aria-label="Message"
				placeholder={over ? "This chat is over." : "Ask your harness…"}
				value={text}
				disabled={over}
				onChange={(event) => {
					setText(event.target.value);
					setCaret(event.target.selectionStart ?? event.target.value.length);
					setPicking(true);
				}}
				onSelect={(event) => {
					const target = event.currentTarget;
					setCaret(target.selectionStart ?? target.value.length);
				}}
				onKeyDown={(event) => {
					if (event.key === "Enter" && !event.shiftKey) {
						event.preventDefault();
						void send();
					}
				}}
			/>
			<div className="mt-1 flex items-center gap-2">
				<p className="text-xs opacity-60">
					Enter sends · Shift+Enter is a new line · @ a task, a feature or a question
				</p>
				{/* (Ending the chat is not the same as a turn ending: its harness stops, and the
				    conversation takes no more turns — so it says that, and asks first.) */}
				{busy && (
					<button
						type="button"
						className="btn btn-ghost btn-sm ml-auto"
						title="End the chat and its harness"
						onClick={() => {
							void onStop();
						}}
					>
						End chat
					</button>
				)}
				<button
					type="button"
					className={`btn btn-primary btn-sm ${busy ? "" : "ml-auto"}`}
					disabled={over || text.trim() === ""}
					onClick={() => {
						void send();
					}}
				>
					{pending > 0 ? `Send (${pending} waiting)` : "Send"}
				</button>
			</div>
		</div>
	);
}
