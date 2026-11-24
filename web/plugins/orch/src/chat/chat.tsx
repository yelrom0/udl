import type { PanelProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useChat, useChatRunners, useKeys } from "../hooks";
import { isChatting, isOver } from "../model";
import { Composer } from "./composer";
import { Chats, Context } from "./header";
import { NewChat } from "./new";
import { Permission } from "./permission";
import { Transcript } from "./transcript";

/**
 * The chat (29.12): a person's own harness, in a panel of the shell's own — the transcript of
 * their conversation, what their harness is waiting on, and the composer.
 *
 * The panel is the shell's (a `panels` contribution): the same chat is drawn here, in the pop-out
 * window and after a reload, because the chat is udl's and the browser only remembers which one
 * it was showing.
 */
export const ChatPanel: Component<PanelProps> = ({ ctx, close, popOut }) => {
	const chat = useChat(ctx);
	const runners = useChatRunners(ctx);
	const keys = useKeys(ctx);
	const said = chat.chat;

	return (
		<section
			className="flex h-full min-h-0 flex-col bg-base-100 text-base-content"
			aria-label="Chat"
		>
			<header className="flex flex-wrap items-center gap-2 border-b border-base-content/20 p-2">
				<h2 className="text-sm font-semibold">Chat</h2>
				<Chats chat={chat} />
				<div className="ml-auto flex items-center gap-1">
					<button
						type="button"
						className="btn btn-ghost btn-xs"
						title="Start a new chat"
						aria-label="Start a new chat"
						onClick={() => {
							void chat.draft();
						}}
					>
						＋
					</button>
					<button
						type="button"
						className="btn btn-ghost btn-xs"
						title="Pop the chat out"
						aria-label="Pop the chat out"
						onClick={popOut}
					>
						↗
					</button>
					<button
						type="button"
						className="btn btn-ghost btn-xs"
						title="Close the chat"
						aria-label="Close the chat"
						onClick={close}
					>
						×
					</button>
				</div>
			</header>
			{said !== null && <Context chat={said} />}
			{chat.error !== null && <p className="px-3 py-2 text-sm text-error">{chat.error}</p>}
			<div className="min-h-0 flex-1 overflow-y-auto">
				{said === null && (
					<NewChat
						runners={runners.items}
						started={chat.loaded}
						onStart={async (request) => {
							await chat.start(request);
						}}
					/>
				)}
				{said !== null && <Transcript chat={said} />}
			</div>
			{said?.permission != null && (
				<Permission
					chat={said}
					onAnswer={async (request, decision, message) => {
						await chat.answer(request, decision, message);
					}}
				/>
			)}
			{said !== null && (
				<Composer
					keys={keys}
					busy={isChatting(said)}
					over={isOver(said)}
					pending={said.pending}
					onSend={async (text) => {
						await chat.send(text);
					}}
					onStop={async () => {
						await chat.end();
					}}
				/>
			)}
		</section>
	);
};
