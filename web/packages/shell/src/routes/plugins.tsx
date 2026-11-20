/**
 * The plugins udl has: the UI plugins the page can load, the bundles udl refused, and the ones
 * this browser could not load. What each one brings, and where.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PluginList } from "../components/PluginList";

export const Route = createFileRoute("/plugins")({
	component: PluginsPage,
});

function PluginsPage() {
	return (
		<section className="my-6">
			<h1 className="text-2xl font-bold">Plugins</h1>
			<PluginList />
		</section>
	);
}
