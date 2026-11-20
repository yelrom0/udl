/**
 * ---------------
 * - generated.ts -
 * ---------------
 *
 * `/ui/generated.json` (24.3), into the page: what each plugin has to show, as the generated
 * pages read it. A list the page cannot read is a problem a page says, never an exception out of
 * a route.
 */

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { type GeneratedPlugin, type GeneratedUI, pluginNamed } from "../generated/spec";

export const generated = createStore<{
	/** What udl answered, when it was a generated UI. */
	ui: GeneratedUI | null;
	/** Why it has none (udl answered something else, or did not answer). */
	error: string | null;
}>(() => ({ ui: null, error: null }));

/** Keep what udl answered (`ui`), or why there is none (`error`). */
export function setGenerated(ui: GeneratedUI | null, error: string | null): void {
	generated.setState({ ui, error });
}

/** The plugin `name`'s page, from what udl answered (`undefined` until it has, or when it has
 * no such plugin). */
export function pluginFor(ui: GeneratedUI | null, name: string): GeneratedPlugin | undefined {
	return ui === null ? undefined : pluginNamed(ui, name);
}

/** The generated UI's plugins, as a hook. */
export const useGenerated = () => useStore(generated, (state) => state.ui);

/** Why the page has no generated UI, as a hook. */
export const useGeneratedError = () => useStore(generated, (state) => state.error);

/** Whether `/ui/generated.json` has been read — answered, or failed: until it has, a page says it
 * is loading rather than that a plugin does not exist. */
export const useGeneratedLoaded = () =>
	useStore(generated, (state) => state.ui !== null || state.error !== null);

/** The plugin `name`'s page, as a hook. */
export function useGeneratedPlugin(name: string): GeneratedPlugin | undefined {
	return useStore(generated, (state) => pluginFor(state.ui, name));
}
