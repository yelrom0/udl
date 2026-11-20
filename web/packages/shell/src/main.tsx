/**
 * The page's start: the theme as the user last chose it (and following the system while that
 * is the choice), then the router's first page.
 */

import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { loadPlugins } from "./plugins/list";
import { onNavigate, routerNavigation } from "./plugins/navigation";
import { router } from "./router";
import { applyTheme, followSystem, theme } from "./stores/theme";
import "./index.css";

const root = document.getElementById("root");
if (root === null) {
	throw new Error("the page has no #root");
}
applyTheme(theme.getState().current);
followSystem();
// (The UI plugins udl lists, into the page's registry: before the first route is drawn, so a
// plugin's nav item is there from the start. A list that cannot be read is a problem the
// Plugins page shows, never a page that fails to open.)
void loadPlugins();
// (A plugin's `ctx.navigate("/sites")` is a path of the page's: the router moves, with its
// basepath, instead of the browser loading `/sites` from udl.)
onNavigate(routerNavigation(router.history));
createRoot(root).render(
	<StrictMode>
		<RouterProvider router={router} />
	</StrictMode>,
);
