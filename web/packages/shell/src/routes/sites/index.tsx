/**
 * The sites page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/sites/")({
	component: () => (
		<Placeholder title="Sites" note="The site list and the per-site pages arrive with Phase 19." />
	),
});
