/**
 * `ctx.call(name, request)`: any of udl's operations, by the name its operationId has, so a
 * plugin reaches the operations its own Python plugin added without the shell knowing them.
 *
 * The request is one flat JSON object, as the SDK's `call` takes it: the keys a path template
 * names (`/downloads/{id}`) fill it, and what is left is the query (a read) or the JSON body (a
 * write).
 */

import { operations } from "@udl/api";
import type { JsonObject, JsonValue } from "@udl/ui-sdk";
import { asError } from "../api";

/** An operation's method and path, as the API's own generated list has them. */
interface Route {
	readonly method: string;
	readonly path: string;
}

// (The generated list's keys are its literals; a plugin names one at run time, so this is the
// shape looked up by any string, and an unknown one is refused below.)
const ROUTES: Readonly<Record<string, Route>> = operations;

/** Whether a method writes (and so needs the CSRF header udl asks for). */
const writes = (method: string): boolean => !["GET", "HEAD", "OPTIONS"].includes(method);

/** Whether a method carries a JSON body (a delete's request is its path). */
const sends = (method: string): boolean => ["POST", "PUT", "PATCH"].includes(method);

/** A query string for what a read's request has left (`?a=1&b=2`, `[]` for nothing). */
function queryOf(fields: JsonObject): string {
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries(fields)) {
		if (value === null || value === undefined) {
			continue;
		}
		if (Array.isArray(value)) {
			for (const one of value) {
				search.append(name, String(one));
			}
			continue;
		}
		search.append(name, typeof value === "object" ? JSON.stringify(value) : String(value));
	}
	const said = search.toString();
	return said === "" ? "" : `?${said}`;
}

/** `path` with its `{name}`s filled from `request`, and what was left of the request. */
function fill(path: string, request: JsonObject): { path: string; rest: JsonObject } {
	const rest: JsonObject = { ...request };
	const filled = path.replace(/\{([^}]+)\}/g, (_whole, name: string) => {
		const value = rest[name];
		if (value === undefined || value === null) {
			throw new Error(`needs its "${name}"`);
		}
		delete rest[name];
		return encodeURIComponent(String(value));
	});
	return { path: filled, rest };
}

/**
 * Call the operation `name` with `request`: its answer as JSON (`null` for a request udl
 * answers with nothing), or an `Error` saying what udl refused it with.
 */
export async function callOperation(
	name: string,
	request: JsonObject = {},
	fetch: typeof globalThis.fetch = (input, init) => globalThis.fetch(input, init),
): Promise<JsonValue> {
	const route = ROUTES[name];
	if (route === undefined) {
		throw new Error(`udl has no operation "${name}"`);
	}
	let filled: { path: string; rest: JsonObject };
	try {
		filled = fill(route.path, request);
	} catch (error) {
		const said = error instanceof Error ? error.message : String(error);
		throw new Error(`${name} ${said}`);
	}
	const method = route.method.toUpperCase();
	const body = sends(method) && Object.keys(filled.rest).length > 0 ? filled.rest : undefined;
	const headers: Record<string, string> = {};
	if (writes(method)) {
		headers["X-UDL-CSRF"] = "1";
	}
	if (body !== undefined) {
		headers["Content-Type"] = "application/json";
	}
	const url = `${filled.path}${sends(method) ? "" : queryOf(filled.rest)}`;
	const response = await fetch(url, {
		method,
		headers,
		credentials: "same-origin",
		...(body === undefined ? {} : { body: JSON.stringify(body) }),
	});
	const said = response.status === 204 ? "" : await response.text();
	let answered: JsonValue = null;
	if (said !== "") {
		try {
			answered = JSON.parse(said);
		} catch {
			answered = said;
		}
	}
	if (!response.ok) {
		throw asError(answered, response);
	}
	return answered;
}
