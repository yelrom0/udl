/**
 * The accounts page. Phase 19 and 20 fill it: the shell makes it reachable now.
 */

import { createFileRoute } from "@tanstack/react-router";
import { Placeholder } from "../../components/Placeholder";

export const Route = createFileRoute("/accounts/")({
	component: () => (
		<Placeholder
			title="Accounts"
			note="Signing udl in to Gmail, Slack and the other integrations arrives with Phase 20."
		/>
	),
});
