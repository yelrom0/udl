/**
 * ---------------
 * - load.ts -
 * ---------------
 *
 * `/ui/generated.json`, into the store: what each plugin has to show, and the drawer's entry for
 * each plugin that has a page of its own. A list the page cannot read is a problem the Plugins
 * page says, never a page that fails to open.
 */

import { registerGenerated } from "../plugins/shell";
import { setGenerated } from "../stores/generated";
import { generatedUI } from "./spec";

/** Where udl lists what a page generates from (24.3). */
export const GENERATED_URL = "/ui/generated.json";

/** Read udl's generated UI, and keep it (or why there is none). */
export async function loadGenerated(
	fetch: typeof globalThis.fetch = (input, init) => globalThis.fetch(input, init),
): Promise<void> {
	try {
		const response = await fetch(GENERATED_URL, { credentials: "same-origin" });
		if (!response.ok) {
			setGenerated(null, `udl answered ${response.status}`);
			registerGenerated(null);
			return;
		}
		const ui = generatedUI(await response.json());
		if (ui === null) {
			setGenerated(null, "udl answered something that is not a generated UI");
			registerGenerated(null);
			return;
		}
		setGenerated(ui, null);
		registerGenerated(ui);
	} catch (error) {
		setGenerated(null, error instanceof Error ? error.message : String(error));
		registerGenerated(null);
	}
}
