/**
 * The settings page. The shell makes it reachable now; its own task fills it.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../components/Placeholder";

export const Route = createFileRoute("/settings")({
	component: () => (
		<Placeholder
			title="Settings"
			note="udl's own config, as the server reads it, arrives with Phase 20."
		/>
	),
});
