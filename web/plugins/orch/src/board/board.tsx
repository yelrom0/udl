import type { RouteProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useBoard } from "../hooks";
import { Page, Said } from "../page";
import { AgentsPane } from "./agents";
import { FeaturesPane } from "./features";
import { Filters } from "./filtersRow";
import { SuggestedPane } from "./suggested";

/** What a task is filtered by: its state, and text in its title. */
function matches(
	task: { readonly state: string; readonly title: string },
	state: string,
	text: string,
): boolean {
	if (state !== "" && task.state !== state) {
		return false;
	}
	return text === "" || task.title.toLowerCase().includes(text.toLowerCase());
}

/**
 * The board (29.4): three panes — the features and their tasks, an agent a column with its
 * queue, and what is suggested next. The filters are in the URL, so a filtered board is a page
 * someone can send.
 */
export const BoardPage: Component<RouteProps> = ({ ctx, search }) => {
	const { project, features, tasks, agents, suggestions, merge, progress, error } = useBoard(ctx);
	const { state, text } = { state: search.state ?? "", text: search.text ?? "" };
	const shown = tasks.filter((task) => matches(task, state, text));
	const reading = project === "" && error === null;
	return (
		<Page title="Board">
			{error !== null && <p className="text-error">{error}</p>}
			{reading && <Said>Reading the board…</Said>}
			{!reading && error === null && (
				<>
					<Filters navigate={ctx.navigate} search={search} />
					<div className="grid gap-4 lg:grid-cols-3">
						<div className="flex flex-col gap-4 lg:col-span-2">
							{shown.length === 0 && (state !== "" || text !== "") && (
								<Said>Nothing matches that.</Said>
							)}
							<FeaturesPane features={features} merge={merge} progress={progress} tasks={shown} />
						</div>
						<div className="flex flex-col gap-4">
							<AgentsPane agents={agents} progress={progress} tasks={tasks} />
							<SuggestedPane suggestions={suggestions} />
						</div>
					</div>
				</>
			)}
		</Page>
	);
};
