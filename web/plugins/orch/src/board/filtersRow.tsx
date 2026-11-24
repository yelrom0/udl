import type { UIContext } from "@udl/ui-sdk";
import { BOARD, filterPath, filtersOf } from "./filters";

/** The states a task can be filtered by, as the board offers them (the Concepts table's). */
const STATES: readonly string[] = [
	"open",
	"queued",
	"in_progress",
	"blocked",
	"ready",
	"merged",
	"held",
	"done",
];

/** The board's filters: a task's state, and text in its title. Each is written into the URL. */
export function Filters({
	search,
	navigate,
}: {
	readonly search: Readonly<Record<string, string>>;
	readonly navigate?: UIContext["navigate"] | undefined;
}) {
	const { state, text } = filtersOf(search);
	const go = (change: Parameters<typeof filterPath>[2]): void => {
		navigate?.(filterPath(BOARD, search, change));
	};
	return (
		<div className="flex flex-wrap items-end gap-2">
			<label className="flex flex-col gap-1 text-xs">
				State
				<select
					aria-label="State"
					className="select select-sm select-bordered"
					onChange={(event) => {
						go({ state: event.target.value });
					}}
					value={state}
				>
					<option value="">Every state</option>
					{STATES.map((one) => (
						<option key={one} value={one}>
							{one}
						</option>
					))}
				</select>
			</label>
			<label className="flex flex-col gap-1 text-xs">
				Text
				<input
					aria-label="Text"
					className="input input-sm input-bordered"
					// (The box is the browser's own, and the URL is written when the typing is done:
					// one history entry per search, not one per letter. It is not a React value,
					// because a keystroke and the blur that follows can be split by a render — a
					// store's read landing between them — and a value React owns would be put back
					// to the last render's text, losing what the person typed.)
					defaultValue={text}
					// (The URL is still the truth: Back and Forward give the box its text again.)
					key={text}
					onBlur={(event) => {
						if (event.target.value !== text) {
							go({ text: event.target.value });
						}
					}}
					onKeyDown={(event) => {
						if (event.key === "Enter" && event.currentTarget.value !== text) {
							go({ text: event.currentTarget.value });
						}
					}}
					placeholder="in a task's title"
					type="search"
				/>
			</label>
		</div>
	);
}
