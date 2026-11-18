/**
 * The downloads page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/downloads/")({
	component: () => (
		<Placeholder
			title="Downloads"
			note="The download list, its filters and its live progress arrive with Phase 19."
		/>
	),
});
