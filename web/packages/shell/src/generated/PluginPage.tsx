/**
 * ----------------
 * - PluginPage.tsx -
 * ----------------
 *
 * A plugin's generated page (24.6): what udl says the plugin has — its operations, its settings,
 * its accounts, its event types — as tabs, each drawn from the schemas udl sent. A plugin whose
 * page is not in the list says so; nothing here throws out of a route.
 */

import type { UdlApi } from "@udl/api";
import { useState } from "react";
import { api as shellApi } from "../api";
import { AccountsTab } from "./Accounts";
import { EventsTab } from "./Events";
import { OperationsTab } from "./Operations";
import { SettingsTab } from "./Settings";
import { type GeneratedPlugin, type Tab, tabsOf } from "./spec";

export interface PluginPageProps {
	readonly plugin: GeneratedPlugin;
	readonly api?: UdlApi;
}

/** The tabs of `plugin`'s page, and the one showing. */
export function PluginPage({ plugin, api = shellApi }: PluginPageProps) {
	const tabs = tabsOf(plugin);
	const [shown, setShown] = useState<Tab | null>(null);
	const tab = shown !== null && tabs.includes(shown) ? shown : (tabs[0] ?? null);

	return (
		<div className="flex flex-col gap-4">
			<header>
				<h2 className="text-2xl font-semibold">{plugin.title}</h2>
				{plugin.summary !== "" && <p className="opacity-70">{plugin.summary}</p>}
			</header>
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
					{tab === "operations" && <OperationsTab plugin={plugin} api={api} />}
					{tab === "settings" && <SettingsTab plugin={plugin} api={api} />}
					{tab === "accounts" && <AccountsTab plugin={plugin} api={api} />}
					{tab === "events" && <EventsTab plugin={plugin} api={api} />}
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
