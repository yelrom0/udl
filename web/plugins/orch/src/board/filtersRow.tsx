import type { UIContext } from "@udl/ui-sdk";
import { useEffect, useState } from "react";
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
	// (The box is typed into freely, and the URL is written when the typing is done: one history
	// entry per search, not one per letter.)
	const [typed, setTyped] = useState(text);
	// (The URL is the truth: Back and Forward put the query's text back in the box.)
	useEffect(() => {
		setTyped(text);
	}, [text]);
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
					onBlur={() => {
						if (typed !== text) {
							go({ text: typed });
						}
					}}
					onChange={(event) => {
						setTyped(event.target.value);
					}}
					onKeyDown={(event) => {
						if (event.key === "Enter" && typed !== text) {
							go({ text: typed });
						}
					}}
					placeholder="in a task's title"
					type="search"
					value={typed}
				/>
			</label>
		</div>
	);
}
