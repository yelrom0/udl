/**
 * The dashboard: what udl has, at a glance — the sites, and the downloads it worked on last.
 * The pages that do the work are the nav's.
 */

import { createFileRoute } from "@tanstack/react-router";

import { api, given } from "../api";
import { ErrorCard } from "../components/ErrorCard";
import { TimeAgo } from "../components/TimeAgo";
import { UDL } from "../plugins/shell";
import { PluginSlot } from "../plugins/slot";
import { useLive } from "../stores/live";

export const Route = createFileRoute("/")({
	loader: async () => {
		const [sites, downloads] = await Promise.all([
			api.client.GET("/sites"),
			api.client.GET("/downloads", { params: { query: { limit: 5 } } }),
		]);
		return {
			sites: given(sites).sites,
			downloads: given(downloads).downloads,
		};
	},
	errorComponent: ({ error }) => <ErrorCard error={error} />,
	component: Dashboard,
});

function Dashboard() {
	const { sites, downloads } = Route.useLoaderData();
	return (
		<section className="my-6">
			<h1 className="text-2xl font-bold">Dashboard</h1>
			<div className="stats mt-4 shadow">
				<div className="stat">
					<div className="stat-title">Sites</div>
					<div className="stat-value">{sites.length}</div>
				</div>
				<div className="stat">
					<div className="stat-title">Downloads</div>
					<div className="stat-value">{downloads.length}</div>
					<div className="stat-desc">the newest five</div>
				</div>
			</div>

			<h2 className="mt-8 text-xl font-semibold">The last downloads</h2>
			{downloads.length === 0 ? (
				<p className="mt-2 opacity-70">udl has downloaded nothing yet.</p>
			) : (
				<table className="table mt-2">
					<thead>
						<tr>
							<th>URL</th>
							<th>Status</th>
							<th>When</th>
						</tr>
					</thead>
					<tbody>
						{downloads.map((download) => (
							<tr key={download.id}>
								<td className="max-w-md truncate">{download.url}</td>
								<td>{download.status}</td>
								<td>
									<TimeAgo at={download.started_at} />
								</td>
							</tr>
						))}
					</tbody>
				</table>
			)}

			{/* (A plugin's own cards: nothing at all when no plugin has a dashboard widget.) */}
			<PluginSlot point="widgets" props={{ slot: "dashboard", plugin: UDL }} />

			<Live />
		</section>
	);
}

/** What udl has said since the page opened: the runs it started, and the events it stored. */
function Live() {
	const connection = useLive((state) => state.connection);
	const runs = useLive((state) => state.runs);
	const events = useLive((state) => state.events);
	const newest = events.slice(-5).reverse();

	return (
		<section className="mt-8">
			<h2 className="text-xl font-semibold">Live</h2>
			<p className="mt-1 text-sm opacity-70">
				{connection === "live"
					? "udl is telling the page what happens, as it happens."
					: "The page is not following udl yet."}
			</p>

			<h3 className="mt-4 font-semibold">Runs</h3>
			{Object.entries(runs).length === 0 ? (
				<p className="text-sm opacity-70">No run has started since the page opened.</p>
			) : (
				<ul className="mt-1 space-y-1">
					{Object.entries(runs)
						.slice(0, 5)
						.map(([id, run]) => (
							<li key={id} className="text-sm">
								<span className="font-mono">{id.slice(0, 8)}</span> {said(run, "summary")}{" "}
								{said(run, "status")}
							</li>
						))}
				</ul>
			)}

			<h3 className="mt-4 font-semibold">Events</h3>
			{newest.length === 0 ? (
				<p className="text-sm opacity-70">Nothing has happened since the page opened.</p>
			) : (
				<ul className="mt-1 space-y-1">
					{newest.map((event, index) => (
						<li key={`${event.at}-${index}`} className="text-sm">
							<TimeAgo at={event.at} /> — {said(event.data, "subject") || event.name}
						</li>
					))}
				</ul>
			)}
		</section>
	);
}

/** A field of what udl sent, when it is a string (an event's subject, a run's summary). */
function said(value: unknown, field: string): string {
	if (!isRecord(value)) {
		return "";
	}
	const found = value[field];
	return typeof found === "string" ? found : "";
}

/** Whether `value` is an object with named fields (what udl's JSON is). */
function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}
