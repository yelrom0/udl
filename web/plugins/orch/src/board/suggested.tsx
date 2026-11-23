import type { Suggestion } from "../model";
import { carry } from "./drops";

/** One suggestion: what it is, why it was made, the agent it suits, and Queue it. */
function Card({
	suggestion,
	onQueue,
}: {
	readonly suggestion: Suggestion;
	readonly onQueue?: ((suggestion: Suggestion) => void) | undefined;
}) {
	return (
		<article
			aria-label={`Suggested: ${suggestion.task} ${suggestion.title}`}
			className="flex flex-col gap-1 rounded-lg border border-base-content/20 p-2"
			draggable
			onDragStart={() => {
				carry({ kind: "suggestion", suggestion });
			}}
		>
			<div className="flex items-baseline gap-2">
				<span className="font-mono text-xs">{suggestion.task}</span>
				<span className="min-w-0 flex-1 truncate text-sm">{suggestion.title}</span>
			</div>
			<p className="text-xs opacity-70">{suggestion.reasons.join(" · ")}</p>
			<div className="flex items-center justify-between gap-2">
				<span className="text-xs opacity-70">{suggestion.agent}</span>
				<button
					className="btn btn-xs btn-outline"
					onClick={() => {
						onQueue?.(suggestion);
					}}
					type="button"
				>
					Queue it
				</button>
			</div>
		</article>
	);
}

/** What is suggested next (27.4): each card says why, and which agent it suits. */
export function SuggestedPane({
	suggestions,
	onQueue,
}: {
	readonly suggestions: readonly Suggestion[];
	readonly onQueue?: ((suggestion: Suggestion) => void) | undefined;
}) {
	return (
		<section aria-label="Suggested" className="flex flex-col gap-2">
			<h2 className="text-xs font-semibold uppercase tracking-wide opacity-60">Suggested</h2>
			{suggestions.length === 0 ? (
				<p className="text-xs opacity-60">Nothing is ready to start.</p>
			) : (
				suggestions.map((suggestion) => (
					<Card key={suggestion.task} onQueue={onQueue} suggestion={suggestion} />
				))
			)}
		</section>
	);
}
