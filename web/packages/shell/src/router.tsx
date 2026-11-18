/**
 * The router: file routes under `src/routes/` (`routeTree.gen.ts` is theirs, generated), at
 * `/ui` — the page's base, since udl serves it there.
 */

import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";

export const router = createRouter({ routeTree, basepath: "/ui" });

declare module "@tanstack/react-router" {
	interface Register {
		router: typeof router;
	}
}
