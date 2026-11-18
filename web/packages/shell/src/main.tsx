/**
 * The page's start: the theme as the user last chose it (and following the system while that
 * is the choice), then the router's first page.
 */

import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { router } from "./router";
import { applyTheme, followSystem, theme } from "./stores/theme";
import "./index.css";

const root = document.getElementById("root");
if (root === null) {
	throw new Error("the page has no #root");
}
applyTheme(theme.getState().current);
followSystem();
createRoot(root).render(
	<StrictMode>
		<RouterProvider router={router} />
	</StrictMode>,
);
