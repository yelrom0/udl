/**
 * The databases page. The shell makes it reachable now; its own task fills it.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../components/Placeholder";

export const Route = createFileRoute("/databases")({
	component: () => (
		<Placeholder
			title="Databases"
			note="The database connections, their migrations and their settings arrive with Phase 19."
		/>
	),
});
