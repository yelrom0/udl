/**
 * The ai page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/ai/")({
	component: () => (
		<Placeholder
			title="AI"
			note="The local model's answers, and the pages that ask it, arrive with Phase 20."
		/>
	),
});
