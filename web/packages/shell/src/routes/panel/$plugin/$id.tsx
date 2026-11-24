/**
 * A panel in a window of its own (29.12): `/ui/panel/<plugin>/<id>`, where the page's own copy
 * is closed as this one opens, so the two cannot disagree about what is being said. It is the
 * panel and nothing else — no nav, no signing out: a window that is one thing.
 *
 * `popOut` here brings the page back (closing this window and focusing the one that opened it),
 * since a panel cannot be popped out of the window it is already its own.
 */

import { createFileRoute, useParams } from "@tanstack/react-router";
import { useEffect } from "react";
import { Placeholder } from "../../../components/Placeholder";
import { panelKey } from "../../../plugins/panels";
import { shellRegistry } from "../../../plugins/shell";
import { PluginSlot } from "../../../plugins/slot";

export const Route = createFileRoute("/panel/$plugin/$id")({
	component: PanelWindow,
});

/** The page this window was opened from, brought forward (a browser without `opener`: nothing). */
function back(): void {
	window.opener?.focus();
	window.close();
}

function PanelWindow() {
	const { plugin, id } = useParams({ from: "/panel/$plugin/$id" });
	const key = panelKey(plugin, id);
	const [panel] = shellRegistry.point("panels").at(key);

	// (The window's own title, so the task switcher says what it is.)
	useEffect(() => {
		if (panel?.title !== undefined) {
			document.title = panel.title;
		}
	}, [panel?.title]);

	if (panel === undefined) {
		return (
			<main className="flex h-screen flex-col p-4">
				<Placeholder
					title={`No panel "${id}"`}
					note={`The ${plugin} UI plugin has no panel called "${id}".`}
				/>
			</main>
		);
	}
	return (
		<main className="flex h-screen flex-col bg-base-200" data-panel={key} data-panel-window="1">
			<PluginSlot point="panels" lookup={key} props={{ open: true, close: back, popOut: back }} />
		</main>
	);
}
