/**
 * What a plugin's `ctx.events` is: the events the page's live connection brings, handed to
 * whoever asked for the ones a filter matches. The connection is one; each plugin's `subscribe`
 * is a listener on it.
 */

import { type EventSource, isJsonObject, type JsonObject, type JsonValue } from "@udl/ui-sdk";

/** A listener: what it asked for, and what to call. */
interface Listener {
	readonly filter: string | JsonObject;
	readonly heard: (event: JsonObject) => void;
}

const listeners = new Set<Listener>();

/** The field `path` names in `event`, following its dots (`data.from`). */
function valueAt(event: JsonObject, path: string): JsonValue | undefined {
	let found: JsonValue = event;
	for (const step of path.split(".")) {
		if (typeof found !== "object" || found === null || Array.isArray(found)) {
			return undefined;
		}
		found = found[step] ?? null;
	}
	return found;
}

/** Whether a filter's value is one of an event's (`["a", "b"]` is any of them). */
function oneOf(wanted: JsonValue, found: JsonValue | undefined): boolean {
	if (Array.isArray(wanted)) {
		return wanted.some((one) => oneOf(one, found));
	}
	return JSON.stringify(wanted) === JSON.stringify(found ?? null);
}

/** Whether a type filter matches: the same type, or a prefix when it ends with a star. */
function typeMatches(wanted: string, type: string): boolean {
	if (wanted === "*") {
		return true;
	}
	if (wanted.endsWith("*")) {
		return type.startsWith(wanted.slice(0, -1));
	}
	return wanted === type;
}

/** Whether `event` is one `filter` asks for: a type (a prefix with a `*`), or its fields. */
export function matches(filter: string | JsonObject, event: JsonObject): boolean {
	if (typeof filter === "string") {
		const type = event.type;
		return typeof type === "string" && typeMatches(filter, type);
	}
	return Object.entries(filter).every(([path, wanted]) =>
		oneOf(wanted ?? null, valueAt(event, path)),
	);
}

/**
 * Hand `event` to every listener whose filter matches it. A listener that throws does not stop
 * the others: a plugin's bug is its own (its slot shows it).
 */
export function publishEvent(event: unknown): void {
	if (!isJsonObject(event)) {
		return;
	}
	for (const listener of [...listeners]) {
		if (!matches(listener.filter, event)) {
			continue;
		}
		try {
			listener.heard(event);
		} catch {
			// (One plugin's listener failing is not another's.)
		}
	}
}

/** The page's one event source, as every plugin's `ctx.events` is. */
export function eventsOf(): EventSource {
	return {
		subscribe(filter: string | JsonObject, callback: (event: JsonObject) => void): () => void {
			const listener: Listener = { filter, heard: callback };
			listeners.add(listener);
			return () => {
				listeners.delete(listener);
			};
		},
	};
}

/** Forget every listener (a page that signs out, and a test's own page). */
export function forgetListeners(): void {
	listeners.clear();
}
