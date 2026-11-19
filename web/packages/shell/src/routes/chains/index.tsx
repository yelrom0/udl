/**
 * The chains page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/chains/")({
	component: () => (
		<Placeholder
			title="Chains"
			note="Building and running a chain of steps arrives with Phase 19."
		/>
	),
});
