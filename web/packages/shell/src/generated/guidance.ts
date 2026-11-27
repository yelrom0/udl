/**
 * --------------
 * - guidance.ts -
 * --------------
 *
 * How a UI plugin guides the page udl generates for its plugin (24.7). Its manifest's
 * `generated` says what to hide, regroup, rename and reorder, which tabs stay, and what its
 * drawer entry is; its three points (`operationViews`, `resultViews`, `fieldWidgets`) replace
 * part of the page, and are drawn by `<PluginSlot>` like any other contribution.
 *
 * Guidance is by operation name, which is udl's own (`gmail_send`). A UI-only manifest (one with
 * no `plugin`) guides every generated page, and a bound one (`"plugin": "gmail"`) only its own
 * plugin's page, applied after the UI-only ones so it has the last word. What names no operation
 * was already said by udl (`problems` on the plugin), and is ignored here.
 */

import type { GeneratedNav } from "@udl/ui-sdk";
import type { UIPluginInfo } from "../stores/plugins";
import type { GeneratedPlugin } from "./spec";
import { type Tab, tabsOf } from "./spec";

/** What every UI plugin's `generated` says, merged: the page's own guidance. */
export interface Guidance {
	/** The operations not to show, a `*` at the end naming a prefix. */
	readonly hide: readonly string[];
	/** The operations to show even so (29.9): what a plugin's own pages do not cover. */
	readonly show: readonly string[];
	/** The operations to move under a group, by group (`Mail: ["gmail_list"]`). */
	readonly groups: Readonly<Record<string, readonly string[]>>;
	/** The groups' own order: the order their keys were given in. */
	readonly groupOrder: readonly string[];
	/** The operations to put first, in this order. */
	readonly order: readonly string[];
	/** What to call an operation, instead of its title. */
	readonly labels: Readonly<Record<string, string>>;
}

/** The guidance nothing says. */
const NOTHING: Guidance = { hide: [], show: [], groups: {}, groupOrder: [], order: [], labels: {} };

/** Whether `pattern` names `name`: itself, or its prefix when it ends with `*`. */
export function matches(pattern: string, name: string): boolean {
	return pattern.endsWith("*") ? name.startsWith(pattern.slice(0, -1)) : pattern === name;
}

/** The guidance `plugin`'s page takes: every UI-only manifest's (they guide every page), then
 * the plugin's own (it is applied last, so it has the last word), each in udl's order (nothing
 * when none gives any). */
export function guidanceOf(manifests: readonly UIPluginInfo[], plugin: string): Guidance {
	const reaching = manifests.filter((manifest) => {
		if (manifest.generated === undefined || manifest.generated === null) {
			return false;
		}
		const bound = manifest.plugin ?? null;
		return bound === null || bound === plugin;
	});
	// (Two groups, each in the order udl listed them: `filter` keeps that order.)
	const ordered = [
		...reaching.filter((manifest) => (manifest.plugin ?? null) === null),
		...reaching.filter((manifest) => (manifest.plugin ?? null) === plugin),
	];
	const hide: string[] = [];
	const show: string[] = [];
	const groups: Record<string, readonly string[]> = {};
	const order: string[] = [];
	const labels: Record<string, string> = {};
	for (const manifest of ordered) {
		const given = manifest.generated;
		if (given === undefined || given === null) {
			continue;
		}
		hide.push(...(given.hide ?? []));
		show.push(...(given.show ?? []));
		// (A group is a name, not an owner: a second manifest naming it adds its members.)
		for (const [group, names] of Object.entries(given.groups ?? {})) {
			const found = groups[group];
			groups[group] = found === undefined ? [...names] : [...found, ...names];
		}
		order.push(...(given.order ?? []));
		Object.assign(labels, given.labels ?? {});
	}
	if (
		hide.length + show.length + order.length === 0 &&
		Object.keys(groups).length + Object.keys(labels).length === 0
	) {
		return NOTHING;
	}
	return { hide, show, groups, groupOrder: Object.keys(groups), order, labels };
}

/** `plugin`'s page as `guidance` asks for it: its hidden operations gone, its own in their
 * groups, what it says called and in its order. */
export function guided(plugin: GeneratedPlugin, guidance: Guidance): GeneratedPlugin {
	if (guidance === NOTHING) {
		return plugin;
	}
	const grouped = new Map<string, string>();
	for (const [group, names] of Object.entries(guidance.groups)) {
		for (const name of names) {
			grouped.set(name, group);
		}
	}
	const first = new Map<string, number>();
	guidance.order.forEach((name, index) => {
		first.set(name, index);
	});
	const operations = plugin.operations
		.filter(
			(operation) =>
				guidance.show.some((pattern) => matches(pattern, operation.name)) ||
				!guidance.hide.some((pattern) => matches(pattern, operation.name)),
		)
		.map((operation) => {
			const group = grouped.get(operation.name);
			const label = guidance.labels[operation.name];
			const place = first.get(operation.name);
			// (What it orders comes first: the rest keep their own order, after all of them.)
			const order =
				place ??
				(guidance.order.length === 0
					? operation.ui.order
					: guidance.order.length + operation.ui.order);
			return {
				...operation,
				...(label === undefined ? {} : { title: label }),
				ui: {
					...operation.ui,
					...(group === undefined ? {} : { group }),
					order,
				},
			};
		});
	return { ...plugin, operations };
}

/** The tabs `plugin`'s page keeps: the ones its own UI plugin names, in its order, each only
 * when the page has something for it (`null`, or an empty list, for all of them as udl lists
 * them: udl sends `[]` for a guidance that does not name them). */
export function tabsGuided(plugin: GeneratedPlugin, tabs: readonly Tab[] | null): readonly Tab[] {
	const has = tabsOf(plugin);
	if (tabs === null || tabs.length === 0) {
		return has;
	}
	return tabs.filter((tab) => has.includes(tab));
}

/** The drawer entry `name`'s own UI plugin asks for: its title and icon, `false` for none, or
 * `null` for udl's rule (an entry when the plugin has a page to go to). */
export function navOf(
	manifests: readonly UIPluginInfo[],
	name: string,
): GeneratedNav | false | null {
	const own = manifests.find((manifest) => (manifest.plugin ?? manifest.name) === name);
	const nav = own?.generated?.nav;
	return nav === undefined || nav === null ? null : nav;
}

/** The tabs `name`'s own UI plugin asks to keep (`null` for all of them, and for an empty list:
 * udl sends `[]` for a guidance that does not name them). */
export function tabsOfGuided(
	manifests: readonly UIPluginInfo[],
	name: string,
): readonly Tab[] | null {
	const own = manifests.find((manifest) => (manifest.plugin ?? manifest.name) === name);
	const tabs = own?.generated?.tabs;
	return tabs === undefined || tabs === null || tabs.length === 0 ? null : tabs;
}
