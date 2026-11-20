/**
 * --------------
 * - nav.ts -
 * --------------
 *
 * The drawer's entries for the plugins whose pages the shell generates (24.6): a plugin with
 * operations, settings or accounts has a page of its own, and so an entry — unless its UI plugin
 * brings its own pages, which are the ones it wants shown.
 *
 * Since 24.7 its UI plugin may also say what the entry is called, with which icon, or that there
 * is none (`generated.nav`), and its guidance may leave the page with nothing on it.
 */

import type { NavEntry } from "../plugins/registry";
import type { UIPluginInfo } from "../stores/plugins";
import { guidanceOf, guided, navOf } from "./guidance";
import type { GeneratedPlugin, GeneratedUI } from "./spec";

/** The shell's own name, as a plugin's (the generated list has it too, and it is no entry). */
const UDL = "udl";

/** Whether `plugin` has a page with something on it (an operation to run, settings, accounts). */
export function hasSomething(plugin: GeneratedPlugin): boolean {
	return (
		plugin.operations.some((operation) => !operation.ui.hidden) ||
		plugin.settings !== null ||
		plugin.accounts !== null
	);
}

/** The entries the generated pages add to the drawer: one per plugin with something to show,
 * except the shell itself and the plugins whose UI plugin brings its own navigation. */
export function generatedNav(ui: GeneratedUI | null, infos: readonly UIPluginInfo[]): NavEntry[] {
	if (ui === null) {
		return [];
	}
	const own = new Set(
		infos.filter((info) => (info.contributes.nav ?? []).length > 0).map((info) => info.name),
	);
	const entries: NavEntry[] = [];
	for (const plugin of ui.plugins) {
		if (plugin.name === UDL) {
			continue;
		}
		const asked = navOf(infos, plugin.name);
		// (No entry when it asks for none, and udl's rule when it says nothing.)
		if (asked === false || (asked === null && own.has(plugin.name))) {
			continue;
		}
		if (!hasSomething(guided(plugin, guidanceOf(infos, plugin.name)))) {
			continue;
		}
		entries.push({
			plugin: { name: plugin.name, version: "", baseUrl: "/ui/" },
			title: asked?.title ?? plugin.title,
			...(asked?.icon === null || asked?.icon === undefined ? {} : { icon: asked.icon }),
			to: `/p/${plugin.name}/`,
		});
	}
	return entries;
}
