import type { JsonObject } from "@udl/ui-sdk";
import { useState } from "react";
import type { Chat } from "../model";
import type { Decision } from "../stores/chat";

/** What a chat's harness wants, in a line: the command, when it is one, or the JSON it gave. */
function wanted(input: JsonObject): string {
	const command = input.command;
	if (typeof command === "string") {
		return command;
	}
	return JSON.stringify(input, null, 2);
}

/**
 * What a chat's harness asked before it acted (29.12, from 27.11's permission prompt): the tool,
 * what it wants to use it on, and where — and three answers: allow it once, allow it for the rest
 * of this chat, or deny it with why.
 *
 * The card is the only thing in the panel that stops the chat: until the person answers, the
 * harness waits, so it is drawn above the composer and nothing else is drawn over it.
 */
export function Permission({
	chat,
	onAnswer,
}: {
	readonly chat: Chat;
	readonly onAnswer: (request: string, decision: Decision, message?: string) => Promise<void>;
}) {
	const asked = chat.permission;
	const [denying, setDenying] = useState(false);
	const [why, setWhy] = useState("");
	const [answering, setAnswering] = useState(false);
	if (asked === null) {
		return null;
	}

	const answer = async (decision: Decision, message = ""): Promise<void> => {
		setAnswering(true);
		try {
			await onAnswer(asked.id, decision, message);
			setDenying(false);
			setWhy("");
		} finally {
			setAnswering(false);
		}
	};

	return (
		<section
			className="m-2 rounded border border-warning/60 bg-base-100 p-3 text-sm shadow-sm"
			aria-label="Waiting on you"
		>
			<h3 className="font-semibold">{`${chat.runner} wants to run ${asked.tool}`}</h3>
			<p className="text-xs opacity-70">
				{`on ${chat.host}, in ${chat.directory} · your ${chat.runner} settings ask before it acts`}
			</p>
			<pre className="mt-2 overflow-x-auto rounded bg-base-200 p-2 font-mono text-xs">
				{wanted(asked.input)}
			</pre>
			{denying ? (
				<div className="mt-2 flex flex-wrap items-end gap-2">
					<label className="flex flex-1 flex-col gap-1 text-xs">
						Why not?
						<textarea
							className="textarea textarea-bordered textarea-xs w-full"
							value={why}
							onChange={(event) => setWhy(event.target.value)}
						/>
					</label>
					<button
						type="button"
						className="btn btn-error btn-sm"
						disabled={answering}
						onClick={() => {
							void answer("deny", why);
						}}
					>
						Deny it
					</button>
					<button
						type="button"
						className="btn btn-ghost btn-sm"
						onClick={() => {
							setDenying(false);
						}}
					>
						Back
					</button>
				</div>
			) : (
				<div className="mt-2 flex flex-wrap gap-2">
					<button
						type="button"
						className="btn btn-primary btn-sm"
						disabled={answering}
						onClick={() => {
							void answer("allow_once");
						}}
					>
						Allow once
					</button>
					<button
						type="button"
						className="btn btn-sm"
						disabled={answering}
						onClick={() => {
							void answer("allow_chat");
						}}
					>
						Allow for this chat
					</button>
					<button
						type="button"
						className="btn btn-ghost btn-sm"
						disabled={answering}
						onClick={() => {
							setDenying(true);
						}}
					>
						Deny
					</button>
				</div>
			)}
		</section>
	);
}
