/**
 * The runs page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/runs/")({
	component: () => (
		<Placeholder
			title="Runs"
			note="A run's steps, their state and their output arrive with Phase 19."
		/>
	),
});
