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
import { ResultView } from "./resultView";
import { Widget } from "./widget";
import "./index.css";

export default defineUIPlugin({
	routes: { index: react(Page) },
	widgets: { template: react(Widget) },
	// (24.7: the page udl generates for another plugin, guided from here — its result drawn by
	// this plugin, and one operation it does not want shown hidden in the manifest.)
	resultViews: { fake_count_things: react(ResultView) },
});
