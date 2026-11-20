/**
 * ------------
 * - Events.tsx -
 * ------------
 *
 * The event types a plugin publishes (24.6), from `list_event_types`: each with what it is, its
 * fields and an example, and the recent ones the page has seen live — the plugin's own, newest
 * first.
 */

import type { UdlApi } from "@udl/api";
import type { JsonObject } from "@udl/ui-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLive } from "../stores/live";
import { jsonObject, saidBy } from "./run";
import type { GeneratedPlugin } from "./spec";

/** What udl says an event type is (`EventTypeInfo`, as the page reads it). */
interface EventType {
	readonly name: string;
	readonly summary: string;
	readonly source: string;
	readonly json_schema: JsonObject;
	readonly example: JsonObject;
}

/** An event the page has seen: its type and when, as the live stream carried it. */
interface Seen {
	readonly type: string;
	readonly at: string;
}

export interface EventsTabProps {
	readonly plugin: GeneratedPlugin;
	readonly api: UdlApi;
}

/** The types `plugin` publishes, and the recent ones it has fired. */
export function EventsTab({ plugin, api }: EventsTabProps) {
	const [types, setTypes] = useState<readonly EventType[] | null>(null);
	const [said, setSaid] = useState<{ message: string; hint: string | null } | null>(null);
	const live = useRef(true);

	useEffect(() => {
		live.current = true;
		return () => {
			live.current = false;
		};
	}, []);

	const read = useCallback(async () => {
		try {
			const answered = await api.call("list_event_types");
			if (live.current) {
				setTypes(
					answered.types
						.filter((one) => one.source === plugin.name)
						.map((one) => ({
							name: one.name,
							summary: one.summary,
							source: one.source,
							json_schema: jsonObject(one.json_schema),
							example: jsonObject(one.example),
						})),
				);
			}
		} catch (error) {
			if (live.current) {
				setSaid(saidBy(error));
			}
		}
	}, [api, plugin.name]);

	useEffect(() => {
		void read();
	}, [read]);

	const seen = useSeen(plugin.name);

	return (
		<div className="flex flex-col gap-6">
			{said !== null && (
				<div role="alert" className="alert alert-error">
					<span>{said.message}</span>
				</div>
			)}
			{types?.length === 0 && <p className="opacity-70">No event types.</p>}
			{types?.map((type) => (
				<article key={type.name} className="card bg-base-200">
					<div className="card-body">
						<h4 className="card-title font-mono text-base">{type.name}</h4>
						{type.summary !== "" && <p className="text-sm opacity-70">{type.summary}</p>}
						<details>
							<summary className="cursor-pointer text-sm">Its fields</summary>
							<pre className="overflow-x-auto rounded bg-base-300 p-2 text-xs">
								{JSON.stringify(type.json_schema, null, 2)}
							</pre>
						</details>
						<details>
							<summary className="cursor-pointer text-sm">An example</summary>
							<pre className="overflow-x-auto rounded bg-base-300 p-2 text-xs">
								{JSON.stringify(type.example, null, 2)}
							</pre>
						</details>
					</div>
				</article>
			))}
			<section>
				<h3 className="mb-2 text-lg font-semibold">Recent</h3>
				{seen.length === 0 ? (
					<p className="opacity-70">Nothing yet.</p>
				) : (
					<ul className="list">
						{seen.map((event, index) => (
							<li key={`${event.at}-${index}`} className="list-row font-mono text-sm">
								<span>{event.type}</span>
								<span className="opacity-70">{event.at}</span>
							</li>
						))}
					</ul>
				)}
			</section>
		</div>
	);
}

/** How many of a plugin's events the page shows. */
export const RECENT = 10;

/** The plugin's recent events, from the live stream, newest first. (The events themselves are
 * what the store's selector picks — a new array every read would re-render forever — and the
 * plugin's are picked out of them here.) */
function useSeen(plugin: string): readonly Seen[] {
	const events = useLive((state) => state.events);
	return recentOf(events, plugin);
}

/** The events `events` has of `plugin`'s types, newest first (at most `RECENT`). */
export function recentOf(
	events: readonly { readonly name: string; readonly at: string; readonly data: unknown }[],
	plugin: string,
): readonly Seen[] {
	const seen: Seen[] = [];
	for (const event of events) {
		const type = typeOf(event.data);
		if (event.name !== "event" || type === null || !type.startsWith(`${plugin}.`)) {
			continue;
		}
		seen.push({ type, at: event.at });
	}
	return seen.slice(-RECENT).reverse();
}

/** The `type` an event's data names, when it names one. */
function typeOf(data: unknown): string | null {
	if (typeof data !== "object" || data === null || !("type" in data)) {
		return null;
	}
	return typeof data.type === "string" ? data.type : null;
}
