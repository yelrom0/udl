import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app";
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
		<App />
	</StrictMode>,
);
