/**
 * The page's one registry, and what fills it: the shell's own pages and widgets (registered at
 * startup) and each UI plugin udl lists (from its manifest, before any bundle is imported).
 */

import { plugins, type UIPluginInfo } from "../stores/plugins";
import { Registry } from "./registry";

/** The page's registry: what the shell and its plugins contribute. */
export const shellRegistry = new Registry();

/** What the shell itself contributes: its name, as a plugin's is. */
export const UDL = { name: "udl", version: "0.0.0", baseUrl: "/ui/" } as const;

/**
 * Register `info`'s contributions, as its manifest says they are: the navigation's pages, and
 * each point's keys. No bundle is imported: a contribution's mount is read when it is drawn.
 */
export function registerPlugin(info: UIPluginInfo): void {
	const identity = { name: info.name, version: info.version, baseUrl: info.base_url };
	const contributes = info.contributes;
	for (const item of contributes.nav ?? []) {
		shellRegistry.nav.push({
			plugin: identity,
			title: item.title,
			to: `/p/${info.name}/${item.route}`,
			...(item.icon === null || item.icon === undefined ? {} : { icon: item.icon }),
		});
	}
	for (const route of contributes.routes ?? []) {
		shellRegistry.point("routes").add(route, { plugin: identity, key: route, entry: route });
	}
	for (const plugin of contributes.settings ?? []) {
		shellRegistry.point("settings").add(plugin, { plugin: identity, key: plugin, entry: plugin });
	}
	for (const integration of contributes.accountSetup ?? []) {
		shellRegistry
			.point("accountSetup")
			.add(integration, { plugin: identity, key: integration, entry: integration });
	}
	for (const type of contributes.eventViews ?? []) {
		shellRegistry.point("eventViews").add(type, { plugin: identity, key: type, entry: type });
	}
	for (const integration of contributes.filterEditors ?? []) {
		shellRegistry
			.point("filterEditors")
			.add(integration, { plugin: identity, key: integration, entry: integration });
	}
	for (const kind of contributes.actionEditors ?? []) {
		shellRegistry.point("actionEditors").add(kind, { plugin: identity, key: kind, entry: kind });
	}
	for (const widget of contributes.widgets ?? []) {
		shellRegistry.point("widgets").add(widget.slot, {
			plugin: identity,
			key: widget.slot,
			id: widget.id,
			entry: widget.id,
		});
	}
}

/**
 * Replace the plugins the page knows: the registry is emptied of every plugin's contributions
 * and filled with what udl lists now (the shell's own stay: they are registered once).
 */
export function registerPlugins(infos: readonly UIPluginInfo[]): void {
	shellRegistry.clear();
	registerOwn(shellRegistry);
	for (const info of infos) {
		registerPlugin(info);
	}
	plugins.setState({ nav: [...shellRegistry.nav] });
}

/** The shell's own pages in the navigation (they are part of the shell, not a bundle). */
export function registerOwn(registry: Registry): void {
	if (registry.nav.some((item) => item.plugin.name === UDL.name)) {
		return;
	}
	for (const item of OWN_NAV) {
		registry.nav.push({ plugin: UDL, title: item.title, to: item.to });
	}
}

/** The shell's own pages: what `__root`'s drawer used to hold as one list. */
export const OWN_NAV: readonly { readonly title: string; readonly to: string }[] = [
	{ to: "/", title: "Dashboard" },
	{ to: "/downloads", title: "Downloads" },
	{ to: "/sites", title: "Sites" },
	{ to: "/chains", title: "Chains" },
	{ to: "/events", title: "Events" },
	{ to: "/subscriptions", title: "Subscriptions" },
	{ to: "/actions", title: "Actions" },
	{ to: "/runs", title: "Runs" },
	{ to: "/schedules", title: "Schedules" },
	{ to: "/ai", title: "AI" },
	{ to: "/accounts", title: "Accounts" },
	{ to: "/plugins", title: "Plugins" },
	{ to: "/databases", title: "Databases" },
	{ to: "/settings", title: "Settings" },
];
