/**
 * The actions page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/actions/")({
	component: () => (
		<Placeholder
			title="Actions"
			note="What a subscription does when it fires arrives with Phase 19."
		/>
	),
});
