/**
 * UI plugins, as the shell knows them: each one's manifest (18.7's `UIManifest`; a small shape
 * until then) and the bundles that would not load. 18.7's `GET /ui/plugins.json` fills it;
 * 18.9 loads them.
 */

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

/** What a UI plugin's manifest says about its nav item and the routes it brings. */
export interface Manifest {
	readonly name: string;
	readonly version: string;
	readonly entry: string;
	readonly styles?: readonly string[];
	readonly isolation?: "shadow" | "none";
	readonly contributes?: {
		readonly nav?: readonly {
			readonly title: string;
			readonly icon?: string;
			readonly route?: string;
		}[];
		readonly routes?: readonly string[];
	};
}

/** A plugin whose bundle the browser could not load, and why. */
export interface PluginError {
	readonly name: string;
	readonly message: string;
}

export const plugins = createStore<{
	manifests: readonly Manifest[];
	errors: readonly PluginError[];
}>(() => ({ manifests: [], errors: [] }));

/** Replace what the page knows about the plugins (after `GET /ui/plugins.json`). */
export function setPlugins(manifests: readonly Manifest[]): void {
	plugins.setState({ manifests });
}

/** Say a plugin's bundle did not load (it stays out of the page, and is listed). */
export function addError(name: string, message: string): void {
	plugins.setState(({ errors }) => ({ errors: [...errors, { name, message }] }));
}

/** The plugins' state, as a hook. */
export const usePlugins = () => useStore(plugins, (state) => state.manifests);

/** The bundles that would not load, as a hook. */
export const usePluginErrors = () => useStore(plugins, (state) => state.errors);
