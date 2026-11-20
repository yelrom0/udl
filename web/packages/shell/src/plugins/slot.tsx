/**
 * `<PluginSlot>`: what draws a contribution point. It looks the contributions up in the
 * registry, imports each plugin's bundle the first time one of its contributions is needed,
 * mounts it in its own shadow root (or the shell's DOM), and turns anything that goes wrong
 * into an error card in that slot — never the end of the page.
 */

import type { Mount, UIPluginSpec } from "@udl/ui-sdk";
import { useEffect, useRef, useState } from "react";
import { ErrorCard } from "../components/ErrorCard";
import { addError, plugins, type UIPluginInfo } from "../stores/plugins";
import { shownTheme, theme as themeStore } from "../stores/theme";
import { contextOf } from "./context";
import { prepare } from "./dom";
import { ErrorBoundary } from "./ErrorBoundary";
import { type Importer, loadPlugin } from "./load";
import { navigate } from "./navigation";
import { type Contribution, keyOf, type Point, type Points, type Registry } from "./registry";
import { shellRegistry } from "./shell";

/** The bundle's record for each point (its entry's `routes`, `widgets`...). */
const RECORDS: {
	[Q in Point]: (spec: UIPluginSpec) => Record<string, Mount<Points[Q]>> | undefined;
} = {
	routes: (spec) => spec.routes,
	settings: (spec) => spec.settings,
	accountSetup: (spec) => spec.accountSetup,
	eventViews: (spec) => spec.eventViews,
	filterEditors: (spec) => spec.filterEditors,
	actionEditors: (spec) => spec.actionEditors,
	widgets: (spec) => spec.widgets,
};

/** What a slot is told: the point, the props that point's contributions take, and how it is
 * looked up (`lookup`: the plugin a route belongs to, the integration an editor is for). */
export type PluginSlotProps<P extends Point> = {
	readonly point: P;
	/** The props that point's contributions take (a route's path, a widget's slot...). */
	readonly props: Points[P];
	/** Where the contributions come from (the page's own registry, unless a test has one). */
	readonly registry?: Registry;
	/** The plugin a route belongs to, or the integration an editor is for. */
	readonly lookup?: string;
	/** How a bundle is imported (the browser's `import()`, unless a test has its own). */
	readonly importer?: Importer;
};

/** `contribution`'s mount, from its plugin's bundle (imported once per bundle). */
async function bundleMount<P extends Point>(
	point: P,
	contribution: Contribution<Points[P]>,
	importer?: Importer,
): Promise<Mount<Points[P]>> {
	const info = plugins.getState().manifests.find((one) => one.name === contribution.plugin.name);
	if (info === undefined) {
		throw new Error("its plugin is not in the page's list any more");
	}
	const spec = importer === undefined ? await loadPlugin(info) : await loadPlugin(info, importer);
	const record = RECORDS[point](spec);
	const key = contribution.entry ?? contribution.key;
	const mount = record?.[key];
	if (mount === undefined) {
		throw new Error(`its bundle has no ${point} called "${key}"`);
	}
	return mount;
}

/** What draws one contribution: its own element, and the lifecycle the mount hands back. */
function Draw<P extends Point>({
	contribution,
	point,
	props,
	importer,
	onFailure,
}: {
	readonly contribution: Contribution<Points[P]>;
	readonly point: P;
	readonly props: Points[P];
	readonly importer: Importer | undefined;
	readonly onFailure: (said: string) => void;
}) {
	const host = useRef<HTMLDivElement>(null);
	const wrapper = useRef<HTMLElement | null>(null);
	const mounted = useRef<{ update(props: Points[P]): void; unmount(): void } | null>(null);
	const latest = useRef(props);
	latest.current = props;
	const name = contribution.plugin.name;

	useEffect(() => {
		const element = host.current;
		if (element === null) {
			return;
		}
		let live = true;
		void (async () => {
			try {
				const info: UIPluginInfo | undefined = plugins
					.getState()
					.manifests.find((one) => one.name === name);
				const mount = contribution.mount ?? (await bundleMount(point, contribution, importer));
				const place = await prepare(element, info, { theme: shownTheme() });
				if (!live) {
					// (Gone before its bundle arrived: the wrapper is not the page's to keep.)
					place.wrapper.remove();
					return;
				}
				wrapper.current = place.wrapper;
				mounted.current = mount(
					place.target,
					latest.current,
					contextOf({
						info: info ?? ownInfo(contribution),
						navigate,
					}),
				);
			} catch (error) {
				if (!live) {
					return;
				}
				const said = error instanceof Error ? error.message : String(error);
				addError(name, said);
				onFailure(`The ${name} UI failed: ${said}`);
			}
		})();
		return () => {
			live = false;
			try {
				mounted.current?.unmount();
			} catch (error) {
				// (A plugin's own teardown failing is its error, and not the page's: it is said,
				// and the slot still goes.)
				addError(name, error instanceof Error ? error.message : String(error));
			}
			mounted.current = null;
			wrapper.current?.remove();
			wrapper.current = null;
		};
	}, [contribution, point, importer, name, onFailure]);

	// (Every render: the props may be new ones (a route's path, a widget's slot), and the mount
	// takes them as one update rather than a fresh mount. An update that throws is that
	// contribution's failure, so it is a card in its place, not the page's error.)
	useEffect(() => {
		try {
			mounted.current?.update(latest.current);
		} catch (error) {
			const said = error instanceof Error ? error.message : String(error);
			addError(name, said);
			onFailure(`The ${name} UI failed: ${said}`);
		}
	});

	useEffect(() => {
		return themeStore.subscribe(() => {
			if (wrapper.current !== null) {
				wrapper.current.dataset.theme = shownTheme();
			}
		});
	}, []);

	return <div ref={host} className="udl-plugin-slot" />;
}

/**
 * One contribution in the page: what draws it, and the card that stands in its place when it
 * fails (with a retry, which is a fresh mount of the same contribution).
 */
function SlotItem<P extends Point>(props: {
	readonly contribution: Contribution<Points[P]>;
	readonly point: P;
	readonly props: Points[P];
	readonly importer: Importer | undefined;
}) {
	const [attempt, setAttempt] = useState(0);
	const [failure, setFailure] = useState<string | null>(null);

	if (failure !== null) {
		return (
			<ErrorCard
				error={new Error(failure)}
				onRetry={() => {
					setFailure(null);
					setAttempt((count) => count + 1);
				}}
			/>
		);
	}
	return <Draw key={attempt} {...props} onFailure={setFailure} />;
}

/** What the shell's own contribution is, as the shape a mount's context takes. */
function ownInfo<P>(contribution: Contribution<P>): UIPluginInfo {
	return {
		name: contribution.plugin.name,
		version: contribution.plugin.version,
		base_url: contribution.plugin.baseUrl,
		entry_url: "",
		styles: [],
		isolation: "none",
		contributes: {},
		plugin: contribution.plugin.name,
	};
}

/**
 * Draw every contribution to `point` for these props: the page's plugins' and its own. What
 * fails is an error card in its own place, and the rest are still drawn.
 */
export function PluginSlot<P extends Point>({
	point,
	props,
	registry = shellRegistry,
	lookup,
	importer,
}: PluginSlotProps<P>) {
	const { key, of } = keyOf(point, props, lookup);
	const found = registry.point(point).at(key);
	const chosen = of === undefined ? found : found.filter((one) => one.plugin.name === of);

	if (chosen.length === 0) {
		return null;
	}
	return (
		<ErrorBoundary>
			{chosen.map((contribution, index) => (
				<SlotItem
					// (A plugin's contributions keep their place: the plugin, its key, and its turn.)
					key={`${contribution.plugin.name}:${contribution.entry ?? contribution.key}:${index}`}
					contribution={contribution}
					point={point}
					props={props}
					importer={importer}
				/>
			))}
		</ErrorBoundary>
	);
}
