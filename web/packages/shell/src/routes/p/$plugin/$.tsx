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
import {
	useGeneratedError,
	useGeneratedLoaded,
	useGeneratedPlugin,
} from "../../../stores/generated";
import { usePlugins } from "../../../stores/plugins";

export const Route = createFileRoute("/p/$plugin/$")({
	component: PluginPage,
});

function PluginPage() {
	const { plugin, _splat } = useParams({ from: "/p/$plugin/$" });
	const search = Route.useSearch();
	const manifests = usePlugins();
	const generated = useGeneratedPlugin(plugin);
	const loaded = useGeneratedLoaded();
	const error = useGeneratedError();
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
				{/* (Every manifest may guide it: a UI-only plugin's as much as its own, 24.7.) */}
				<GeneratedPage plugin={generated} manifests={manifests} />
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
	// (Before `/ui/generated.json` has answered — a reload, a direct link — the page does not yet
	// know whether the plugin exists; and a list that could not be read is said as itself.)
	if (!loaded) {
		return (
			<section className="my-6">
				<output className="loading loading-spinner" aria-label="Loading the plugin" />
			</section>
		);
	}
	if (error !== null) {
		return (
			<section className="my-6">
				<div role="alert" className="alert alert-error">
					<span>{error}</span>
				</div>
			</section>
		);
	}
	return (
		<section className="my-6">
			<NoSuchPlugin name={plugin} />
		</section>
	);
}
