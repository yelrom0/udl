import type { WidgetProps } from "@udl/ui-sdk";
import type { Component } from "@udl/ui-sdk/react";
import { useInbox } from "./hooks";

/**
 * The dashboard's orch widget (29.2): the inbox's counts, so the shell's dashboard says what
 * waits on a person without opening the plugin's page. 29.3 gives it the inbox's own cards.
 */
export const Widget: Component<WidgetProps> = ({ ctx }) => {
	const { inbox, error } = useInbox(ctx);
	return (
		<div className="udl-card card-body gap-2 p-4">
			<h2 className="card-title text-base">Orchestrator</h2>
			{error !== null && <p className="text-sm text-error">{error}</p>}
			{error === null && inbox === null && (
				<p className="text-sm opacity-70">Reading what waits on a person…</p>
			)}
			{error === null && inbox !== null && (
				<ul className="flex flex-col gap-1 text-sm">
					<li>{`${inbox.merge.ready.length} ready to merge`}</li>
					<li>
						{inbox.questions.length === 1 ? "1 question" : `${inbox.questions.length} questions`}
					</li>
					<li>{`${inbox.actions.length} to action`}</li>
				</ul>
			)}
		</div>
	);
};
