/**
 * ---------------
 * - Events.test.tsx -
 * ---------------
 *
 * A plugin's event types, from `list_event_types`: its own and no other's, their examples, and
 * the ones the page has seen live — newest first, at most ten, and only its own.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import type { components } from "@udl/api";
import { FakeUdl } from "@udl/api/testing";
import { beforeEach, describe, expect, it } from "vitest";
import { clear, pushEvent } from "../stores/live";
import { EventsTab, RECENT, recentOf } from "./Events";
import type { GeneratedPlugin } from "./spec";

function plugin(): GeneratedPlugin {
	return {
		name: "gmail",
		kind: "udl.integrations",
		title: "Gmail",
		summary: "",
		has_ui_plugin: false,
		operations: [],
		settings: null,
		accounts: null,
		event_types: ["gmail.message"],
	};
}

function type(name: string, source: string): components["schemas"]["EventTypeInfo"] {
	return {
		name,
		source,
		summary: `${name} happened`,
		fields: [],
		json_schema: {},
		example: { id: "1" },
	};
}

beforeEach(() => {
	clear();
});

describe("a plugin's event types", () => {
	it("lists its own, with an example", async () => {
		const fake = new FakeUdl();
		fake.handle("list_event_types", () => ({
			types: [type("gmail.message", "gmail"), type("sites.changed", "udl")],
		}));
		render(<EventsTab plugin={plugin()} api={fake.api()} />);

		expect(await screen.findByText("gmail.message")).toBeDefined();
		expect(screen.getByText("gmail.message happened")).toBeDefined();
		expect(screen.queryByText("sites.changed")).toBeNull();

		fireEvent.click(screen.getByText("An example"));
		expect(screen.getByText(/"id": "1"/)).toBeDefined();
	});

	it("says it has none", async () => {
		const fake = new FakeUdl();
		fake.handle("list_event_types", () => ({ types: [] }));
		render(<EventsTab plugin={plugin()} api={fake.api()} />);

		expect(await screen.findByText("No event types.")).toBeDefined();
	});

	it("says what udl said when it could not list them", async () => {
		const fake = new FakeUdl();
		fake.handle("list_event_types", () => {
			throw new Error("the event log is off");
		});
		render(<EventsTab plugin={plugin()} api={fake.api()} />);

		expect(await screen.findByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("the event log is off"),
		);
	});

	it("draws nothing when the page closes while the types load", async () => {
		const fake = new FakeUdl();
		const waiting: { finish?: (value: components["schemas"]["EventTypeList"]) => void } = {};
		fake.handle(
			"list_event_types",
			() =>
				new Promise<components["schemas"]["EventTypeList"]>((settle) => {
					waiting.finish = settle;
				}),
		);
		const view = render(<EventsTab plugin={plugin()} api={fake.api()} />);
		view.unmount();
		waiting.finish?.({ types: [] });

		await new Promise((settle) => setTimeout(settle, 0));
		expect(fake.calls.filter((call) => call.id === "list_event_types")).toHaveLength(1);
	});

	it("shows the ones it has seen live, and nothing before them", async () => {
		const fake = new FakeUdl();
		fake.handle("list_event_types", () => ({ types: [] }));
		render(<EventsTab plugin={plugin()} api={fake.api()} />);

		expect(await screen.findByText("Nothing yet.")).toBeDefined();
	});

	it("draws the ones it has seen live", async () => {
		const fake = new FakeUdl();
		fake.handle("list_event_types", () => ({ types: [] }));
		pushEvent({ name: "event", at: "2026-01-01T00:00:00Z", data: { type: "gmail.message" } });
		render(<EventsTab plugin={plugin()} api={fake.api()} />);

		expect(await screen.findByText("2026-01-01T00:00:00Z")).toBeDefined();
		expect(screen.getByText("gmail.message")).toBeDefined();
	});

	it("takes the plugin's own from the live stream, newest first", () => {
		const events = [
			{ name: "event", at: "2026-01-01T00:00:00Z", data: { type: "gmail.message" } },
			{ name: "event", at: "2026-01-01T00:00:01Z", data: { type: "sites.changed" } },
			{ name: "event", at: "2026-01-01T00:00:02Z", data: { type: "gmail.thread" } },
			// (A stream's other events — a run's, a download's — are not an event's.)
			{ name: "run", at: "2026-01-01T00:00:03Z", data: { type: "gmail.nope" } },
		];

		expect(recentOf(events, "gmail")).toEqual([
			{ type: "gmail.thread", at: "2026-01-01T00:00:02Z" },
			{ type: "gmail.message", at: "2026-01-01T00:00:00Z" },
		]);
	});

	it("keeps the last ten", () => {
		const events = Array.from({ length: RECENT + 5 }, (_, index) => ({
			name: "event",
			at: `2026-01-01T00:00:${String(index).padStart(2, "0")}Z`,
			data: { type: "gmail.message" },
		}));

		const seen = recentOf(events, "gmail");

		expect(seen).toHaveLength(RECENT);
		expect(seen[0]?.at).toBe("2026-01-01T00:00:14Z");
	});

	it("leaves out an event whose data names no type", () => {
		expect(
			recentOf(
				[
					{ name: "event", at: "2026-01-01T00:00:00Z", data: null },
					{ name: "event", at: "2026-01-01T00:00:01Z", data: { type: 7 } },
					{ name: "event", at: "2026-01-01T00:00:02Z", data: {} },
				],
				"gmail",
			),
		).toEqual([]);
	});
});
