/**
 * A plugin's own page: `p/<plugin>/<anything>`, the route a plugin's nav item and links go to.
 *
 * When its UI plugin lists the path (`inbox`, `message/$id`), the contribution is drawn in its
 * own shadow root, with the path's `$name`s (18.9's `<PluginSlot>`). When it does not — no UI
 * plugin, or no such page — the page is the one udl generates from what the plugin has (24.6),
 * and a plugin udl does not list says so.
 */

import { createFileRoute, useParams } from "@tanstack/react-router";
import { Placeholder } from "../../../components/Placeholder";
import { PluginPage as GeneratedPage, NoSuchPlugin } from "../../../generated/PluginPage";
import { matchRoute, searchOf } from "../../../plugins/routes";
import { PluginSlot } from "../../../plugins/slot";
import { useGeneratedPlugin } from "../../../stores/generated";
import { usePlugins } from "../../../stores/plugins";

export const Route = createFileRoute("/p/$plugin/$")({
	component: PluginPage,
});

function PluginPage() {
	const { plugin, _splat } = useParams({ from: "/p/$plugin/$" });
	const search = Route.useSearch();
	const manifests = usePlugins();
	const generated = useGeneratedPlugin(plugin);
	const known = manifests.find((manifest) => manifest.name === plugin);

	if (known !== undefined) {
		const matched = matchRoute(known.contributes.routes ?? [], _splat ?? "");
		if (matched !== null) {
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
	}
	if (generated !== undefined) {
		return (
			<section className="my-6">
				<GeneratedPage plugin={generated} />
			</section>
		);
	}
	if (known !== undefined) {
		return (
			<Placeholder
				title={`${known.name} ${known.version}`}
				note={`This plugin has no page "${_splat ?? ""}".`}
			/>
		);
	}
	return (
		<section className="my-6">
			<NoSuchPlugin name={plugin} />
		</section>
	);
}
