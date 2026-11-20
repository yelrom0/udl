/**
 * ----------------
 * - index (shadcn) -
 * ----------------
 *
 * 18.10's proof: a UI plugin written with React and shadcn/ui, built by Vite with a Tailwind 4
 * setup of its own. It contributes a page, a dashboard widget and one operation's result (24.8),
 * and its styles never leave its shadow root.
 */

import { defineUIPlugin } from "@udl/ui-sdk";
import { react } from "@udl/ui-sdk/react";
import { Gallery } from "./gallery";
import { Panel } from "./panel";
import { ResultView } from "./resultView";
import "./index.css";

export default defineUIPlugin({
	routes: { gallery: react(Gallery) },
	widgets: { "shadcn-card": react(Panel) },
	resultViews: { fake_list_things: react(ResultView) },
});
