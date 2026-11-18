/**
 * The plugins page. The shell makes it reachable now; its own task fills it.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../components/Placeholder";

export const Route = createFileRoute("/plugins")({
	component: () => (
		<Placeholder
			title="Plugins"
			note="The plugins udl has, the UI they bring, and their settings arrive with 18.7 and 18.9."
		/>
	),
});
