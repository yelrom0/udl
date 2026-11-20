/**
 * A plugin's own page: `p/<plugin>/<anything>`, the route a plugin's nav item and links go to.
 * The path is one its manifest lists (`inbox`, `message/$id`); its `$name`s are what the
 * contribution is drawn with, in its own shadow root (18.9's `<PluginSlot>`).
 */

import { createFileRoute, useParams } from "@tanstack/react-router";
import { Placeholder } from "../../../components/Placeholder";
import { matchRoute, searchOf } from "../../../plugins/routes";
import { PluginSlot } from "../../../plugins/slot";
import { usePlugins } from "../../../stores/plugins";

export const Route = createFileRoute("/p/$plugin/$")({
	component: PluginPage,
});

function PluginPage() {
	const { plugin, _splat } = useParams({ from: "/p/$plugin/$" });
	const search = Route.useSearch();
	const manifests = usePlugins();
	const known = manifests.find((manifest) => manifest.name === plugin);

	if (known === undefined) {
		return (
			<Placeholder
				title={plugin}
				note={`No UI plugin named "${plugin}" is loaded; the Plugins page says which ones are.`}
			/>
		);
	}
	const matched = matchRoute(known.contributes.routes ?? [], _splat ?? "");
	if (matched === null) {
		return (
			<Placeholder
				title={`${known.name} ${known.version}`}
				note={`This plugin has no page "${_splat ?? ""}".`}
			/>
		);
	}
	return (
		<section className="my-6">
			<PluginSlot
				point="routes"
				lookup={plugin}
				props={{ path: matched.route, params: matched.params, search: searchOf(search) }}
			/>
		</section>
	);
}
