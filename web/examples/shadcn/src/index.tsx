/**
 * ----------------
 * - index (shadcn) -
 * ----------------
 *
 * 18.10's proof: a UI plugin written with React and shadcn/ui, built by Vite with a Tailwind 4
 * setup of its own. It contributes a page and a dashboard widget, and its styles never leave
 * its shadow root.
 */

import { defineUIPlugin } from "@udl/ui-sdk";
import { react } from "@udl/ui-sdk/react";
import { Gallery } from "./gallery";
import { Panel } from "./panel";
import "./index.css";

export default defineUIPlugin({
	routes: { gallery: react(Gallery) },
	widgets: { "shadcn-card": react(Panel) },
});
