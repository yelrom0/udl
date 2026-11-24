import type { ChatView } from "../hooks";
import type { Chat } from "../model";

/** The chats this person has, by the one that is drawn: a way back to a conversation. */
export function Chats({ chat }: { readonly chat: ChatView }) {
	if (chat.chats.length === 0) {
		return null;
	}
	return (
		<select
			className="select select-xs max-w-40"
			aria-label="Chats"
			value={chat.chat?.id ?? ""}
			onChange={(event) => {
				const picked = event.target.value;
				if (picked !== "") {
					void chat.pick(picked);
				}
			}}
		>
			{chat.chat === null && <option value="">A new chat</option>}
			{chat.chats.map((one) => (
				<option key={one.id} value={one.id}>
					{one.title === "" ? one.runner : one.title}
				</option>
			))}
		</select>
	);
}

/**
 * What the chat is, in a line: its model, where it runs, its folder, and whose it is. The folder
 * is the one it was started in, so a person reading an old chat knows where it worked.
 */
export function Context({ chat }: { readonly chat: Chat }) {
	return (
		<p className="flex flex-wrap items-center gap-1 px-3 py-1 text-xs opacity-70">
			<span>{chat.model === "" ? chat.runner : chat.model}</span>
			<span aria-hidden="true">·</span>
			<span>{chat.host}</span>
			<span aria-hidden="true">·</span>
			<span className="truncate font-mono">{chat.directory}</span>
			<span aria-hidden="true">·</span>
			<span>{`as ${chat.owner}`}</span>
		</p>
	);
}
