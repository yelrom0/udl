/**
 * A panel in a window of its own (29.12): `/ui/panel/<plugin>/<id>`, where the page's own copy
 * is closed as this one opens, so the two cannot disagree about what is being said. It is the
 * panel and nothing else — no nav, no signing out: a window that is one thing.
 *
 * A window opens the shell from scratch, so it has no plugins on its first draw: it waits for
 * udl's list (the spinner) before it says a panel is missing, as a plugin's own page does.
 *
 * `popOut` here brings the page back (closing this window and focusing the one that opened it),
 * since a panel cannot be popped out of the window it is already its own.
 */

import { createFileRoute, useParams } from "@tanstack/react-router";
import { useEffect } from "react";
import { Placeholder } from "../../../components/Placeholder";
import type { Importer } from "../../../plugins/load";
import { panelKey } from "../../../plugins/panels";
import type { Registry } from "../../../plugins/registry";
import { shellRegistry } from "../../../plugins/shell";
import { PluginSlot } from "../../../plugins/slot";
import { usePlugins, usePluginsLoaded } from "../../../stores/plugins";

export const Route = createFileRoute("/panel/$plugin/$id")({
	component: PanelWindow,
});

/** The page this window was opened from, brought forward (a browser without `opener`: nothing). */
export function back(): void {
	window.opener?.focus();
	window.close();
}

function PanelWindow() {
	const { plugin, id } = useParams({ from: "/panel/$plugin/$id" });
	return <PanelWindowView plugin={plugin} id={id} />;
}

/**
 * The window's drawing, as the route's parameters give it (`plugin` and `id`), with the registry
 * and importer a test can supply.
 */
export function PanelWindowView({
	plugin,
	id,
	registry = shellRegistry,
	importer,
}: {
	readonly plugin: string;
	readonly id: string;
	readonly registry?: Registry;
	readonly importer?: Importer;
}): React.ReactElement {
	const key = panelKey(plugin, id);
	// (The manifests, so this window draws again once udl's list has answered.)
	const manifests = usePlugins();
	const loaded = usePluginsLoaded();
	const [panel] = registry.point("panels").at(key);

	// (The window's own title, so the task switcher says what it is.)
	useEffect(() => {
		if (panel?.title !== undefined) {
			document.title = panel.title;
		}
	}, [panel?.title]);

	if (panel === undefined) {
		// (Before the list has answered, this window does not yet know whether the plugin exists:
		// a reload, a direct link, and the window `popOut` opens all start here.)
		if (!loaded) {
			return (
				<main className="flex h-screen items-center justify-center">
					<output className="loading loading-spinner" aria-label="Loading the panel" />
				</main>
			);
		}
		const known = manifests.find((manifest) => manifest.name === plugin);
		return (
			<main className="flex h-screen flex-col p-4">
				<Placeholder
					title={known === undefined ? `No plugin "${plugin}"` : `No panel "${id}"`}
					note={
						known === undefined
							? `No UI plugin called "${plugin}" is loaded in this window.`
							: `The ${plugin} UI plugin has no panel called "${id}".`
					}
				/>
			</main>
		);
	}
	return (
		<main className="flex h-screen flex-col bg-base-200" data-panel={key} data-panel-window="1">
			<PluginSlot
				point="panels"
				lookup={key}
				registry={registry}
				importer={importer}
				props={{ open: true, close: back, popOut: back }}
			/>
		</main>
	);
}
