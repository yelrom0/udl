/**
 * --------------
 * - nav.ts -
 * --------------
 *
 * The drawer's entries for the plugins whose pages the shell generates (24.6): a plugin with
 * operations, settings or accounts has a page of its own, and so an entry — unless its UI plugin
 * brings its own pages, which are the ones it wants shown.
 */

import type { NavEntry } from "../plugins/registry";
import type { UIPluginInfo } from "../stores/plugins";
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
	return ui.plugins
		.filter((plugin) => plugin.name !== UDL && !own.has(plugin.name) && hasSomething(plugin))
		.map((plugin) => ({
			plugin: { name: plugin.name, version: "", baseUrl: "/ui/" },
			title: plugin.title,
			to: `/p/${plugin.name}/`,
		}));
}
