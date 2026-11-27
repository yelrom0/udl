/**
 * The orchestrator's own UI plugin (29.2): the pages the shell draws for `/ui/p/orch/*`, and the
 * dashboard widget that says what waits on a person. Every mount is a React component from this
 * bundle (`@udl/ui-sdk/react`), so the plugin carries its own React and the shell hands it the
 * context — the API, the live events and the theme — on every draw.
 */

import type { Mount, OperationViewProps, ResultViewProps, RouteProps } from "@udl/ui-sdk";
import { defineUIPlugin } from "@udl/ui-sdk";
import { react } from "@udl/ui-sdk/react";
import "./index.css";
import { ChatPanel } from "./chat/chat";
import { ImportReportView } from "./import/report";
import { Machines } from "./machines/machines";
import { pages } from "./pages";
import { Widget } from "./widget";

/** Every page, as the shell mounts one. */
const routes: Record<string, Mount<RouteProps>> = {};
for (const [route, Page] of Object.entries(pages)) {
	routes[route] = react(Page);
}

/** The plugin itself: its routes, its widget, its chat (29.12's panel), and the two generated
 * pages it draws itself (29.9): the machines, and an import's report. */
export default defineUIPlugin({
	routes,
	widgets: { orch: react(Widget) },
	panels: { chat: react(ChatPanel) },
	operationViews: { orch_list_runners: react<OperationViewProps>(Machines) },
	resultViews: { orch_import_plan: react<ResultViewProps>(ImportReportView) },
});
