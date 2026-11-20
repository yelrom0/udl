/**
 * The shell's own pages, and its own name: what the drawer shows before any plugin's list has
 * been read (and what every list is drawn beside). It is a module of its own so the plugins'
 * store can start with the shell's pages, without importing the registry that fills it.
 */

import type { NavEntry } from "./registry";

/** What the shell itself contributes: its name, as a plugin's is. */
export const UDL = { name: "udl", version: "0.0.0", baseUrl: "/ui/" } as const;

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

/** The shell's own pages, as the navigation's entries (what a page has before the list is read). */
export function ownNav(): NavEntry[] {
	return OWN_NAV.map((item) => ({ plugin: UDL, title: item.title, to: item.to }));
}
