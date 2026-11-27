/**
 * The plugins the page knows, as udl lists them at `/ui/plugins.json`: each one's manifest (what
 * it is, and what it adds), the bundles udl refused (and why), and the bundles that would not
 * load in this browser.
 */

import type { Contributions, Generated } from "@udl/ui-sdk";
import { useMemo } from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { ownNav } from "../plugins/own";
import type { NavEntry } from "../plugins/registry";

/** A UI plugin the shell can load: where its files are, and what it adds (18.7's
 * `UIPluginInfo`). */
export interface UIPluginInfo {
	readonly name: string;
	readonly version: string;
	/** Where its files are served from: `/ui/plugins/<name>/`. */
	readonly base_url: string;
	/** Its ES module. */
	readonly entry_url: string;
	/** Its stylesheets, in the order they are adopted. */
	readonly styles: readonly string[];
	readonly isolation: "shadow" | "none";
	readonly contributes: Contributions;
	/** The plugin it belongs to (`null` for a UI-only plugin). */
	readonly plugin: string | null;
	/** How it guides the page udl generates for its plugin (24.7); absent when it does not. */
	readonly generated?: Generated | null;
	/** What its guidance names that no operation it reaches has (24.7): said, never fatal. */
	readonly problems?: readonly string[];
}

/** A bundle udl refused: where it was, and what was wrong with it. */
export interface BundleProblem {
	readonly origin: string;
	readonly error: string;
}

/** A plugin whose bundle this browser could not load, and why. */
export interface PluginError {
	readonly name: string;
	readonly message: string;
}

export const plugins = createStore<{
	manifests: readonly UIPluginInfo[];
	problems: readonly BundleProblem[];
	errors: readonly PluginError[];
	/** Whether udl's list has answered (`/ui/plugins.json`): before it does, no plugin is
	 * missing — the page just does not know yet. */
	loaded: boolean;
	/** The navigation's pages: the shell's own, then each plugin's (18.9's registry fills it). */
	nav: readonly NavEntry[];
	/** A live count per nav entry, by `<plugin>:<route>`: what a plugin said with
	 * `ctx.setNavCount` (29.7's question count). */
	counts: Readonly<Record<string, number>>;
}>(() => ({
	manifests: [],
	problems: [],
	errors: [],
	loaded: false,
	nav: ownNav(),
	counts: {},
}));

/** Replace what the page knows about the plugins (after `GET /ui/plugins.json`). */
export function setPlugins(
	manifests: readonly UIPluginInfo[],
	problems: readonly BundleProblem[],
): void {
	plugins.setState({ manifests, problems, loaded: true });
}

/** Say a plugin's bundle did not load (it stays out of the page, and is listed once). */
export function addError(name: string, message: string): void {
	plugins.setState(({ errors }) => {
		if (errors.some((error) => error.name === name && error.message === message)) {
			return { errors };
		}
		return { errors: [...errors, { name, message }] };
	});
}

/** The plugins' state, as a hook. */
export const usePlugins = () => useStore(plugins, (state) => state.manifests);

/** Whether udl's list has answered, as a hook (a page that says a plugin is missing waits). */
export const usePluginsLoaded = () => useStore(plugins, (state) => state.loaded);

/** The bundles udl refused, as a hook. */
export const useProblems = () => useStore(plugins, (state) => state.problems);

/** The key a plugin's nav entry is counted under: its own route, as its manifest declares it. */
function countKey(entry: NavEntry): string | null {
	return entry.route === undefined ? null : `${entry.plugin.name}:${entry.route}`;
}

/** The pages with the live counts their plugins set, if they set one (the shell's own pages are
 * never counted: nothing sets a count for them). */
export function navWithCounts(
	nav: readonly NavEntry[],
	counts: Readonly<Record<string, number>>,
): readonly NavEntry[] {
	return nav.map((entry) => {
		const key = countKey(entry);
		const count = key === null ? undefined : counts[key];
		return count === undefined ? entry : { ...entry, count };
	});
}

/** The navigation's pages, as a hook: each with the live count its plugin set, if it set one. */
export function useNav(): readonly NavEntry[] {
	const nav = useStore(plugins, (state) => state.nav);
	const counts = useStore(plugins, (state) => state.counts);
	return useMemo(() => navWithCounts(nav, counts), [nav, counts]);
}

/** Say a plugin's live count on one of its nav entries (`null` takes it off). */
export function setNavCount(plugin: string, route: string, count: number | null): void {
	plugins.setState(({ counts }) => {
		const key = `${plugin}:${route}`;
		if (count === null) {
			const { [key]: gone, ...rest } = counts;
			return { counts: rest };
		}
		return { counts: { ...counts, [key]: count } };
	});
}

/** The bundles that would not load, as a hook. */
export const usePluginErrors = () => useStore(plugins, (state) => state.errors);
