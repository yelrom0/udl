/**
 * The dashboard: what udl has, at a glance — the sites, and the downloads it worked on last.
 * The pages that do the work are the nav's.
 */

import { createFileRoute } from "@tanstack/react-router";

import { api, given } from "../api";
import { ErrorCard } from "../components/ErrorCard";
import { Placeholder } from "../components/Placeholder";
import { TimeAgo } from "../components/TimeAgo";

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

			<Placeholder
				title="Live data"
				note="The runs, jobs and events stream arrives with 18.6: the page will keep itself current."
			/>
		</section>
	);
}
