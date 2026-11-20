/**
 * ----------------
 * - PluginPage.tsx -
 * ----------------
 *
 * A plugin's generated page (24.6): what udl says the plugin has — its operations, its settings,
 * its accounts, its event types — as tabs, each drawn from the schemas udl sent. A plugin whose
 * page is not in the list says so; nothing here throws out of a route.
 *
 * Since 24.7 its UI plugin guides it: what to hide, regroup, rename and reorder, which tabs stay,
 * and its own panels in place of udl's (24.7's three points) — and what its guidance names that
 * the plugin has not is said, once, at the top.
 */

import type { UdlApi } from "@udl/api";
import { useState } from "react";
import { api as shellApi } from "../api";
import type { Registry } from "../plugins/registry";
import { shellRegistry } from "../plugins/shell";
import type { UIPluginInfo } from "../stores/plugins";
import { AccountsTab } from "./Accounts";
import { EventsTab } from "./Events";
import { guidanceOf, guided, tabsGuided, tabsOfGuided } from "./guidance";
import { OperationsTab } from "./Operations";
import { SettingsTab } from "./Settings";
import type { GeneratedPlugin, Tab } from "./spec";

export interface PluginPageProps {
	readonly plugin: GeneratedPlugin;
	readonly api?: UdlApi;
	/** The UI plugins udl lists, whose guidance and contributions the page follows (24.7). */
	readonly manifests?: readonly UIPluginInfo[];
	/** Where the contributions are (a test's own; the shell's when none is given). */
	readonly registry?: Registry;
}

/** The tabs of `plugin`'s page, and the one showing. */
export function PluginPage({
	plugin,
	api = shellApi,
	manifests = [],
	registry = shellRegistry,
}: PluginPageProps) {
	// (Its guidance is the page's: udl's answer, as its UI plugin asks for it.)
	const guidance = guidanceOf(manifests, plugin.name);
	const page = guided(plugin, guidance);
	const tabs = tabsGuided(page, tabsOfGuided(manifests, plugin.name));
	const problems = plugin.problems ?? [];
	const [shown, setShown] = useState<Tab | null>(null);
	const tab = shown !== null && tabs.includes(shown) ? shown : (tabs[0] ?? null);

	return (
		<div className="flex flex-col gap-4">
			<header>
				<h2 className="text-2xl font-semibold">{plugin.title}</h2>
				{plugin.summary !== "" && <p className="opacity-70">{plugin.summary}</p>}
			</header>
			{problems.length > 0 && (
				<div role="alert" className="alert alert-warning">
					<span>
						{`Its UI plugin's guidance names what ${plugin.title} has not: `}
						{problems.join("; ")}.
					</span>
				</div>
			)}
			{tabs.length === 0 ? (
				<p className="opacity-70">This plugin has nothing to show.</p>
			) : (
				<>
					<div role="tablist" className="tabs tabs-boxed w-fit">
						{tabs.map((one) => (
							<button
								key={one}
								type="button"
								role="tab"
								className={`tab${one === tab ? " tab-active" : ""}`}
								aria-selected={one === tab}
								onClick={() => setShown(one)}
							>
								{one}
							</button>
						))}
					</div>
					{tab === "operations" && (
						<OperationsTab
							plugin={page}
							api={api}
							groupOrder={guidance.groupOrder}
							registry={registry}
						/>
					)}
					{tab === "settings" && <SettingsTab plugin={page} api={api} registry={registry} />}
					{tab === "accounts" && <AccountsTab plugin={page} api={api} registry={registry} />}
					{tab === "events" && <EventsTab plugin={page} api={api} />}
				</>
			)}
		</div>
	);
}

/** What a page says for a plugin udl does not list (a link from an older page, a typo). */
export function NoSuchPlugin({ name }: { readonly name: string }) {
	return (
		<section className="flex flex-col gap-4">
			<h1 className="text-2xl font-bold">{name}</h1>
			<div className="alert">
				<span>
					There is no plugin named <span className="font-mono">{name}</span>.
				</span>
			</div>
		</section>
	);
}
