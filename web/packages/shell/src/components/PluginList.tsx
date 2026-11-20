/**
 * What the Plugins page lists: each UI plugin the page can load (what it is, and what it
 * adds), the bundles udl refused, and the ones this browser could not load. A plugin with no
 * web UI is not a problem: it is used through the CLI, the API and MCP.
 */

import { Link } from "@tanstack/react-router";
import { hasSomething } from "../generated/nav";
import { useGenerated, useGeneratedError } from "../stores/generated";
import { usePluginErrors, usePlugins, useProblems } from "../stores/plugins";

/** What a plugin adds, in a line each (`routes: 3`, `settings: gmail`). */
function adds(contributes: {
	readonly nav?: readonly unknown[];
	readonly routes?: readonly string[];
	readonly settings?: readonly string[];
	readonly accountSetup?: readonly string[];
	readonly eventViews?: readonly string[];
	readonly filterEditors?: readonly string[];
	readonly actionEditors?: readonly string[];
	readonly widgets?: readonly unknown[];
	readonly themes?: readonly string[];
}): string[] {
	const said: string[] = [];
	const count = (label: string, items: readonly unknown[] | undefined): void => {
		if (items !== undefined && items.length > 0) {
			said.push(`${label}: ${items.length}`);
		}
	};
	count("nav items", contributes.nav);
	count("pages", contributes.routes);
	count("widgets", contributes.widgets);
	count("themes", contributes.themes);
	const names = (label: string, items: readonly string[] | undefined): void => {
		if (items !== undefined && items.length > 0) {
			said.push(`${label}: ${items.join(", ")}`);
		}
	};
	names("settings", contributes.settings);
	names("account setup", contributes.accountSetup);
	names("event views", contributes.eventViews);
	names("filter editors", contributes.filterEditors);
	names("action editors", contributes.actionEditors);
	return said;
}

/** What a plugin has to show, in a line (`operations: 4`, `settings`, `accounts`, `events: 2`). */
function whatItHas(plugin: {
	readonly operations: readonly { readonly ui: { readonly hidden: boolean } }[];
	readonly settings: unknown;
	readonly accounts: unknown;
	readonly event_types: readonly string[];
}): string {
	const shown = plugin.operations.filter((operation) => !operation.ui.hidden).length;
	const said: string[] = [];
	if (shown > 0) said.push(`operations: ${shown}`);
	if (plugin.settings !== null) said.push("settings");
	if (plugin.accounts !== null) said.push("accounts");
	if (plugin.event_types.length > 0) said.push(`events: ${plugin.event_types.length}`);
	return said.length === 0 ? "nothing to show" : said.join(", ");
}

export function PluginList() {
	const manifests = usePlugins();
	const problems = useProblems();
	const errors = usePluginErrors();

	const generated = useGenerated();
	const generatedError = useGeneratedError();

	return (
		<>
			<h2 className="mt-6 text-xl font-semibold">Plugins</h2>
			{generated === null ? (
				<p className="mt-2 opacity-70">
					{generatedError ?? "udl has not said what its plugins have to show yet."}
				</p>
			) : (
				<table className="table mt-2">
					<thead>
						<tr>
							<th>Plugin</th>
							<th>What it has</th>
							<th />
						</tr>
					</thead>
					<tbody>
						{generated.plugins.map((plugin) => {
							// (A string route, as the drawer's entries are: the plugin's name is udl's.)
							const to: string = `/p/${plugin.name}/`;
							return (
								<tr key={plugin.name}>
									<td>
										<Link to={to} className="link font-semibold">
											{plugin.title}
										</Link>
										<div className="font-mono text-xs opacity-60">{plugin.name}</div>
									</td>
									<td className="text-sm">{whatItHas(plugin)}</td>
									<td>
										{hasSomething(plugin) ? (
											<Link to={to} className="btn btn-xs">
												Open
											</Link>
										) : null}
									</td>
								</tr>
							);
						})}
					</tbody>
				</table>
			)}

			<h2 className="mt-8 text-xl font-semibold">Web UIs</h2>
			{manifests.length === 0 ? (
				<p className="mt-2 opacity-70">
					No plugin brings a web UI. A plugin without one is used through the CLI, the API and MCP.
				</p>
			) : (
				<table className="table mt-2">
					<thead>
						<tr>
							<th>Plugin</th>
							<th>Version</th>
							<th>Isolation</th>
							<th>What it adds</th>
						</tr>
					</thead>
					<tbody>
						{manifests.map((manifest) => (
							<tr key={manifest.name}>
								<td className="font-semibold">{manifest.name}</td>
								<td>{manifest.version}</td>
								<td>{manifest.isolation}</td>
								<td className="text-sm">
									{adds(manifest.contributes).map((line) => (
										<div key={line}>{line}</div>
									))}
								</td>
							</tr>
						))}
					</tbody>
				</table>
			)}

			{problems.length === 0 ? null : (
				<>
					<h2 className="mt-8 text-xl font-semibold">Refused bundles</h2>
					<ul className="mt-2 space-y-1">
						{problems.map((problem) => (
							<li key={`${problem.origin}:${problem.error}`} className="text-sm">
								<span className="font-mono">{problem.origin}</span>: {problem.error}
							</li>
						))}
					</ul>
				</>
			)}

			{errors.length === 0 ? null : (
				<>
					<h2 className="mt-8 text-xl font-semibold">Bundles this browser could not load</h2>
					<ul className="mt-2 space-y-1">
						{errors.map((error) => (
							<li key={error.name} className="text-sm">
								<span className="font-semibold">{error.name}</span>: {error.message}
							</li>
						))}
					</ul>
				</>
			)}
		</>
	);
}
