/**
 * What a plugin's `ctx.events` is: the events the page's live connection brings, handed to
 * whoever asked for the ones a filter matches. The connection is one; each plugin's `subscribe`
 * is a listener on it.
 */

import type { JsonObject } from "@udl/ui-sdk";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { eventsOf, forgetListeners, publishEvent } from "./events";

const RECEIVED = {
	id: "1",
	type: "gmail.message.received",
	data: { subject: "Hello", from: "ada@example.test" },
	occurred_at: "2026-11-20T00:00:00Z",
};

beforeEach(forgetListeners);

describe("a plugin's event source", () => {
	it("hands an event to a listener that asked for its type", () => {
		const heard = vi.fn();
		eventsOf().subscribe("gmail.message.received", heard);

		publishEvent(RECEIVED);

		expect(heard).toHaveBeenCalledWith(RECEIVED);
	});

	it("hands it to a listener whose filter ends with a star", () => {
		const heard = vi.fn();
		eventsOf().subscribe("gmail.*", heard);

		publishEvent(RECEIVED);

		expect(heard).toHaveBeenCalledWith(RECEIVED);
	});

	it("hands nothing to a listener another type is for", () => {
		const heard = vi.fn();
		eventsOf().subscribe("slack.*", heard);

		publishEvent(RECEIVED);

		expect(heard).not.toHaveBeenCalled();
	});

	it("matches a filter object against the event's fields, dotted paths included", () => {
		const heard = vi.fn();
		eventsOf().subscribe(
			{ type: "gmail.message.received", "data.from": "ada@example.test" },
			heard,
		);

		publishEvent(RECEIVED);

		expect(heard).toHaveBeenCalledWith(RECEIVED);
	});

	it("says nothing when a filter object names a field the event does not have", () => {
		const heard = vi.fn();
		eventsOf().subscribe({ "data.to": "bob@example.test" }, heard);

		publishEvent(RECEIVED);

		expect(heard).not.toHaveBeenCalled();
	});

	it("takes a filter's list as any of its values", () => {
		const heard = vi.fn();
		eventsOf().subscribe({ type: ["slack.message", "gmail.message.received"] }, heard);

		publishEvent(RECEIVED);

		expect(heard).toHaveBeenCalled();
	});

	it("stops handing them over when the listener says so", () => {
		const heard = vi.fn();
		const stop = eventsOf().subscribe("gmail.*", heard);

		stop();
		publishEvent(RECEIVED);

		expect(heard).not.toHaveBeenCalled();
	});

	it("says nothing to a listener whose filter is a type it does not match", () => {
		const heard = vi.fn();
		eventsOf().subscribe("gmail.message.received", heard);

		publishEvent({ type: "gmail.message.sent" });

		expect(heard).not.toHaveBeenCalled();
	});

	it("keeps a listener that throws from stopping the others", () => {
		const after = vi.fn();
		eventsOf().subscribe("gmail.*", () => {
			throw new Error("the plugin's own bug");
		});
		eventsOf().subscribe("gmail.*", after);

		expect(() => {
			publishEvent(RECEIVED);
		}).not.toThrow();
		expect(after).toHaveBeenCalled();
	});

	it("forgets what is not an object", () => {
		const heard = vi.fn();
		eventsOf().subscribe("*", heard);

		publishEvent("a string, not an event");
		publishEvent(RECEIVED as JsonObject);

		expect(heard).toHaveBeenCalledTimes(1);
	});
});
