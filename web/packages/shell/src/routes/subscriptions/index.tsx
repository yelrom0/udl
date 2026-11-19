/**
 * The subscriptions page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/subscriptions/")({
	component: () => (
		<Placeholder
			title="Subscriptions"
			note="The subscriptions a site's events feed arrive with Phase 19."
		/>
	),
});
