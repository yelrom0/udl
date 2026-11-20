/**
 * --------------
 * - run.ts -
 * --------------
 *
 * Running an operation a page was generated for (24.6): its form's values split into what its
 * route takes in the path and what is its body, and the error udl answered with, as a page says
 * it — its message and its hint.
 */

import { operations, type UdlApi } from "@udl/api";
import type { JsonObject, JsonValue } from "@udl/ui-sdk";
import type { GeneratedOperation } from "./spec";

/** The names of an operation's path parameters (`/accounts/{account}/check` → `account`). A
 * plugin's operation names its own route; udl's own are the API's (looked up by name). */
export function pathNames(operation: GeneratedOperation | string): readonly string[] {
	const name = typeof operation === "string" ? operation : operation.name;
	const own = typeof operation === "string" ? null : operation.path;
	const route =
		own ??
		(Object.hasOwn(operations, name) ? operations[name as keyof typeof operations].path : "");
	return [...route.matchAll(/\{(\w+)\}/g)].map((match) => match[1] ?? "");
}

/** What an operation is sent: its path's parameters, and the rest of its values as its body. */
export function requestFor(
	operation: GeneratedOperation | string,
	values: JsonObject,
): { params: Record<string, string>; body: JsonObject | undefined } {
	const names = new Set(pathNames(operation));
	const params: Record<string, string> = {};
	const body: JsonObject = {};
	for (const [field, value] of Object.entries(values)) {
		if (names.has(field)) {
			params[field] = value === null || value === undefined ? "" : String(value);
		} else {
			body[field] = value;
		}
	}
	return { params, body: Object.keys(body).length === 0 ? undefined : body };
}

/** Run `operation` with `values` (its form's), and answer what udl said. */
export async function runOperation(
	api: UdlApi,
	operation: GeneratedOperation,
	values: JsonObject,
): Promise<unknown> {
	const { params, body } = requestFor(operation, values);
	// (A plugin's operation has a route of its own, which the API's schema does not hold: it is
	// called by that. udl's own are hand-written routes, which the client knows by name.)
	if (operation.method !== null && operation.path !== null) {
		// (A body is what its route asks for, even when every field may be left out. The client
		// makes a read's fields its query, whether the route is the plugin's or the API's.)
		return api.callAt(operation.method, operation.path, body ?? {}, params);
	}
	return api.callNamed(operation.name, body, params);
}

/** `value` as JSON (`null` for anything JSON cannot hold): what a page draws a result from. */
export function jsonValue(value: unknown): JsonValue {
	if (value === null || typeof value === "string" || typeof value === "number") {
		return value;
	}
	if (typeof value === "boolean") {
		return value;
	}
	if (Array.isArray(value)) {
		return value.map((one: unknown) => jsonValue(one));
	}
	if (typeof value === "object") {
		return Object.fromEntries(
			Object.entries(value).map(([key, one]) => [key, jsonValue(one)] as const),
		);
	}
	return null;
}

/** `value` as a JSON object (`{}` for anything that is not one): a schema, an example. */
export function jsonObject(value: unknown): JsonObject {
	const one = jsonValue(value);
	return typeof one === "object" && one !== null && !Array.isArray(one) ? one : {};
}

/** What udl said about a request it refused: its message, and its hint when it has one. */
export function saidBy(error: unknown): { message: string; hint: string | null } {
	if (typeof error === "object" && error !== null && "info" in error) {
		const info = error.info;
		if (typeof info === "object" && info !== null && "message" in info) {
			const said = info as { message: unknown; hint?: unknown };
			return {
				message: typeof said.message === "string" ? said.message : String(said.message),
				hint: typeof said.hint === "string" ? said.hint : null,
			};
		}
	}
	return { message: error instanceof Error ? error.message : String(error), hint: null };
}
