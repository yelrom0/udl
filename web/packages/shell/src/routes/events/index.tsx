/**
 * The events page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/events/")({
	component: () => (
		<Placeholder
			title="Events"
			note="The event log, filtered by kind and site, arrives with Phase 19."
		/>
	),
});
