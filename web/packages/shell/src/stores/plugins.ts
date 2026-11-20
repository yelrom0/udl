/**
 * The plugins the page knows, as udl lists them at `/ui/plugins.json`: each one's manifest (what
 * it is, and what it adds), the bundles udl refused (and why), and the bundles that would not
 * load in this browser.
 */

import type { Contributions, Generated } from "@udl/ui-sdk";
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
	/** The navigation's pages: the shell's own, then each plugin's (18.9's registry fills it). */
	nav: readonly NavEntry[];
}>(() => ({ manifests: [], problems: [], errors: [], nav: ownNav() }));

/** Replace what the page knows about the plugins (after `GET /ui/plugins.json`). */
export function setPlugins(
	manifests: readonly UIPluginInfo[],
	problems: readonly BundleProblem[],
): void {
	plugins.setState({ manifests, problems });
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

/** The bundles udl refused, as a hook. */
export const useProblems = () => useStore(plugins, (state) => state.problems);

/** The navigation's pages, as a hook. */
export const useNav = () => useStore(plugins, (state) => state.nav);

/** The bundles that would not load, as a hook. */
export const usePluginErrors = () => useStore(plugins, (state) => state.errors);
