/**
 * A plugin's own page: `p/<plugin>/<anything>`, the route a plugin's nav item and links go
 * to. 18.9 mounts the plugin's bundle here; until then (and for a plugin that brings no route
 * of its own, 24.1's amendment) the page says which plugin was asked for.
 */

import { createFileRoute, useParams } from "@tanstack/react-router";
import { Placeholder } from "../../../components/Placeholder";
import { usePlugins } from "../../../stores/plugins";

export const Route = createFileRoute("/p/$plugin/$")({
	component: PluginPage,
});

function PluginPage() {
	const { plugin, _splat } = useParams({ from: "/p/$plugin/$" });
	const manifests = usePlugins();
	const known = manifests.find((manifest) => manifest.name === plugin);

	return (
		<Placeholder
			title={known ? `${known.name} ${known.version}` : plugin}
			note={
				known
					? `The page "${_splat ?? ""}" this plugin brings is mounted here by 18.9.`
					: `No UI plugin named "${plugin}" is loaded; 18.7's list says which ones are.`
			}
		/>
	);
}
