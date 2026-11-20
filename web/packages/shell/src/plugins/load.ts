/**
 * Loading a UI plugin's bundle: its entry's ES module, imported once however many of its
 * contributions the page draws. A failed import is dropped from the cache, so the error card's
 * retry asks again instead of replaying the failure.
 */

import type { UIPluginSpec } from "@udl/ui-sdk";
import type { UIPluginInfo } from "../stores/plugins";

/** How a bundle is imported (a test's own, and the browser's `import()`). */
export type Importer = (url: string) => Promise<unknown>;

/** The browser's `import()`: a URL of the page's own origin, built when it is imported. */
const importing: Importer = (url) => import(/* @vite-ignore */ url);

const cache = new Map<string, Promise<UIPluginSpec>>();

/** What a module is, as a plugin's entry: its default export. */
function specOf(module: unknown, url: string): UIPluginSpec {
	if (typeof module !== "object" || module === null || !("default" in module)) {
		throw new Error(`${url} exports no plugin: its default export is what the shell mounts`);
	}
	const spec = module.default;
	if (typeof spec !== "object" || spec === null) {
		throw new Error(`${url} exports no plugin: its default export is what the shell mounts`);
	}
	return spec as UIPluginSpec;
}

/**
 * `info`'s bundle, as its contributions. It is imported once: a second call (another slot
 * drawing another of its contributions) gets the same promise. A failure is not remembered: the
 * entry is dropped from the cache, so a retry (the error card's) can work.
 */
export function loadPlugin(
	info: UIPluginInfo,
	importer: Importer = importing,
): Promise<UIPluginSpec> {
	const found = cache.get(info.entry_url);
	if (found !== undefined) {
		return found;
	}
	const loading = importer(info.entry_url)
		.then((module) => specOf(module, info.entry_url))
		.catch((error: unknown) => {
			// (A bundle that failed once may be fixed by a retry; the page is not the thing to reload.)
			cache.delete(info.entry_url);
			throw error;
		});
	cache.set(info.entry_url, loading);
	return loading;
}

/** Forget every bundle (a page that signs out, and a test's own page). */
export function forgetPlugins(): void {
	cache.clear();
}
