/**
 * The schedules page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/schedules/")({
	component: () => (
		<Placeholder
			title="Schedules"
			note="The scheduler's jobs, and running one now, arrive with Phase 20."
		/>
	),
});
