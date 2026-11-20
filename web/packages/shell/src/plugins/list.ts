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

/** Whether `value` is what udl says a plugin is (`UIPluginInfo`, 18.7). */
function isPlugin(value: unknown): value is UIPluginInfo {
	if (typeof value !== "object" || value === null) {
		return false;
	}
	const one = value as Record<string, unknown>;
	return (
		typeof one.name === "string" &&
		typeof one.version === "string" &&
		typeof one.base_url === "string" &&
		typeof one.entry_url === "string" &&
		Array.isArray(one.styles) &&
		one.styles.every((style: unknown) => typeof style === "string") &&
		(one.isolation === "shadow" || one.isolation === "none") &&
		typeof one.contributes === "object" &&
		one.contributes !== null &&
		(one.plugin === null || typeof one.plugin === "string") &&
		// (Its guidance is read tolerantly where it is used: this only says it is an object.)
		(one.generated === undefined || one.generated === null || typeof one.generated === "object")
	);
}

/** Whether `value` is what udl says a refused bundle is (`UIBundleProblem`, 18.7). */
function isProblem(value: unknown): value is BundleProblem {
	if (typeof value !== "object" || value === null) {
		return false;
	}
	const one = value as Record<string, unknown>;
	return typeof one.origin === "string" && typeof one.error === "string";
}

/** The list, when `value` is one (`null` when udl answered something else). */
function pluginList(value: unknown): PluginList | null {
	if (typeof value !== "object" || value === null) {
		return null;
	}
	const one = value as Record<string, unknown>;
	if (!Array.isArray(one.plugins) || !one.plugins.every(isPlugin)) {
		return null;
	}
	const problems = one.problems ?? [];
	if (!Array.isArray(problems) || !problems.every(isProblem)) {
		return null;
	}
	return { plugins: one.plugins, problems };
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
		const list = pluginList(await response.json());
		if (list === null) {
			// (Not a list at all: said as a problem, and the shell's own pages are all there are.)
			setPlugins(
				[],
				[{ origin: PLUGINS_URL, error: "udl answered something that is not a plugin list" }],
			);
			registerPlugins([]);
			return;
		}
		setPlugins(list.plugins, list.problems);
		registerPlugins(list.plugins);
	} catch (error) {
		const said = error instanceof Error ? error.message : String(error);
		setPlugins([], [{ origin: PLUGINS_URL, error: said }]);
		registerPlugins([]);
	}
}
