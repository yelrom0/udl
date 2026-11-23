import type { JsonObject, JsonValue, UIContext } from "@udl/ui-sdk";

/** What a fake call answers: the operation's name and request, and what udl would say. */
export type Answer = (name: string, request: JsonObject) => JsonValue | Promise<JsonValue>;

/** A listener the fake event source keeps. */
interface Listener {
	readonly filter: string | JsonObject;
	readonly heard: (event: JsonObject) => void;
}

/** A fake `UIContext`: its calls recorded, and its events publishable, for a store's tests. */
export interface Fake {
	readonly ctx: UIContext;
	readonly calls: { name: string; request: JsonObject }[];
	emit(type: string, data?: JsonObject): void;
	listeners(): number;
}

/** The field `path` names in `event`, following its dots (`data.agent`). */
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

/** Whether `event` is one `filter` asks for: a type (a `prefix*`, or `*`), or its fields. */
export function matches(filter: string | JsonObject, event: JsonObject): boolean {
	if (typeof filter === "string") {
		if (filter === "*") {
			return true;
		}
		const type = event.type;
		if (typeof type !== "string") {
			return false;
		}
		return filter.endsWith("*") ? type.startsWith(filter.slice(0, -1)) : filter === type;
	}
	return Object.entries(filter).every(
		([path, wanted]) => JSON.stringify(wanted ?? null) === JSON.stringify(valueAt(event, path)),
	);
}

/** A fake context whose `call` answers through `answer`, and whose events a test publishes. */
export function fakeCtx(answer: Answer): Fake {
	const calls: { name: string; request: JsonObject }[] = [];
	const kept = new Set<Listener>();
	const ctx: UIContext = {
		api: null,
		call: async (name, request) => {
			const body = request ?? {};
			calls.push({ name, request: body });
			return await answer(name, body);
		},
		events: {
			subscribe(filter, callback) {
				const listener: Listener = { filter, heard: callback };
				kept.add(listener);
				return () => {
					kept.delete(listener);
				};
			},
		},
		theme: { mode: "light", name: "light", tokens: {}, onChange: () => () => {} },
		stores: {},
		navigate: () => {},
		toast: () => {},
		confirm: async () => true,
		plugin: { name: "orch", version: "0.1.0", baseUrl: "/ui/plugins/orch/" },
	};
	return {
		ctx,
		calls,
		emit(type, data) {
			const event: JsonObject = { type, data: data ?? {} };
			for (const listener of [...kept]) {
				if (matches(listener.filter, event)) {
					listener.heard(event);
				}
			}
		},
		listeners: () => kept.size,
	};
}

/** Wait for the promises a store started to settle (its load is not awaited by its maker). */
export async function settle(): Promise<void> {
	await new Promise((done) => setTimeout(done, 0));
}
