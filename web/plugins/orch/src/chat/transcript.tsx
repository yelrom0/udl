import { Markdown } from "../markdown";
import type { Chat } from "../model";
import { blocksOf } from "./blocks";

/** A tool call as one line: what it did, and — opened — whether it worked. */
function Tool({
	name,
	summary,
	ok,
}: {
	readonly name: string;
	readonly summary: string;
	readonly ok: boolean | null;
}) {
	return (
		<details className="rounded border border-base-content/10 bg-base-200/50 px-2 py-1 text-sm">
			<summary className="cursor-pointer select-none">
				<span className="font-mono text-xs">{name}</span>
				{summary !== "" && <span className="opacity-70">{` — ${summary}`}</span>}
				{ok === false && <span className="ml-1 text-error">failed</span>}
				{ok === true && <span className="ml-1 opacity-60">done</span>}
			</summary>
			<p className="pt-1 text-xs opacity-70">
				{ok === null ? "Still running." : ok ? "It worked." : "It failed."}
			</p>
		</details>
	);
}

/**
 * The chat's transcript (29.12): what the person said on the right, what the harness answered on
 * the left, and each tool it ran as one line that opens. The person's words are drawn as they
 * typed them (never as markdown: it is their text, not the model's).
 */
export function Transcript({ chat }: { readonly chat: Chat }) {
	const blocks = blocksOf(chat.events);
	return (
		<section className="flex flex-col gap-3 p-3" aria-label="Transcript">
			{blocks.length === 0 && (
				<p className="text-sm opacity-70">
					{chat.last_text !== "" ? chat.last_text : "Nothing said yet."}
				</p>
			)}
			{blocks.map((block, index) => {
				const key = `${block.kind}-${index}`;
				if (block.kind === "said") {
					return (
						<p
							key={key}
							className="ml-6 self-end whitespace-pre-wrap rounded-lg bg-base-200 px-3 py-2 text-sm"
						>
							{block.text}
						</p>
					);
				}
				if (block.kind === "text") {
					return <Markdown key={key} text={block.text} />;
				}
				if (block.kind === "tool") {
					return <Tool key={key} name={block.name} summary={block.summary} ok={block.ok} />;
				}
				return (
					<p key={key} className="text-xs text-warning">
						{block.text}
					</p>
				);
			})}
			{chat.pending > 0 && (
				<p className="self-end text-xs opacity-70">
					{chat.pending === 1
						? "1 more waiting its turn"
						: `${chat.pending} more waiting their turn`}
				</p>
			)}
		</section>
	);
}
