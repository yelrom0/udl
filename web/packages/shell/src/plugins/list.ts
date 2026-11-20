/**
 * `/ui/plugins.json`, into the store: the UI plugins the page may load, and the bundles udl
 * refused. A list the page cannot read is a problem it shows, never a page that fails to open.
 */

import { type BundleProblem, setPlugins, type UIPluginInfo } from "../stores/plugins";
import { registerPlugins } from "./shell";

/** Where udl lists the UI plugins (18.7). */
export const PLUGINS_URL = "/ui/plugins.json";

/** What `/ui/plugins.json` answers (`UIPluginList`). */
export interface PluginList {
	readonly plugins: readonly UIPluginInfo[];
	readonly problems: readonly BundleProblem[];
}

/** The list, or a problem that says why the page has none. */
export async function loadPlugins(
	fetch: typeof globalThis.fetch = (input, init) => globalThis.fetch(input, init),
): Promise<void> {
	try {
		const response = await fetch(PLUGINS_URL, { credentials: "same-origin" });
		if (!response.ok) {
			setPlugins([], [{ origin: PLUGINS_URL, error: `udl answered ${response.status}` }]);
			registerPlugins([]);
			return;
		}
		const list = (await response.json()) as PluginList;
		setPlugins(list.plugins, list.problems);
		registerPlugins(list.plugins);
	} catch (error) {
		const said = error instanceof Error ? error.message : String(error);
		setPlugins([], [{ origin: PLUGINS_URL, error: said }]);
		registerPlugins([]);
	}
}
