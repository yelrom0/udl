/**
 * The router: file routes under `src/routes/` (`routeTree.gen.ts` is theirs, generated), at
 * `/ui` — the page's base, since udl serves it there.
 */

import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";

/** The page's base: udl serves the shell at `/ui/`, and its routes are paths of that. */
export const BASE = "/ui";

export const router = createRouter({ routeTree, basepath: BASE });

declare module "@tanstack/react-router" {
	interface Register {
		router: typeof router;
	}
}
