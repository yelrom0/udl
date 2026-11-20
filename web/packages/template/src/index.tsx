/**
 * ----------------
 * - index (template) -
 * ----------------
 *
 * A starting point for a udl UI plugin: change the manifest's name and what it contributes, then
 * the page and the widget. `pnpm build` writes `dist/template/` — a folder of bundles, as
 * `[web] ui_plugin_dirs` reads one.
 */

import { defineUIPlugin } from "@udl/ui-sdk";
import { react } from "@udl/ui-sdk/react";
import { Page } from "./page";
import { Widget } from "./widget";
import "./index.css";

export default defineUIPlugin({
	routes: { index: react(Page) },
	widgets: { template: react(Widget) },
});
