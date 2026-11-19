/**
 * The shell's client of udl's API: the typed one from `@udl/api`, at the page's own origin
 * (the API serves the page), signed in by the session's cookie. A write carries the CSRF
 * header the API asks for (the client adds it).
 */

import { createUdlClient } from "@udl/api";

/** What to do when udl answers a request with a 401: the page's session is gone (`stores/session`
 * says which page to show; `api` itself knows nothing of it, so the two do not import each
 * other in a circle). */
let unauthorized: () => void = () => {};

/** Say what a 401 from udl means (the session store sets the page to the login page). */
export function onUnauthorized(handler: () => void): void {
	unauthorized = handler;
}

export const api = createUdlClient({
	baseUrl: window.location.origin,
	// (`fetch` as it is when a request is made, not when this module was read: a test's own
	// `fetch` replaces it, and so would a service worker's.)
	fetch: async (input, init) => {
		const answer = await globalThis.fetch(input, init);
		if (answer.status === 401) {
			unauthorized();
		}
		return answer;
	},
});

/**
 * What udl answered, or the error it answered with: a page shows the error with a retry rather
 * than an empty list that looks like "nothing there".
 */
export function given<T>(answer: { data?: T; error?: unknown; response?: Response }): T {
	if (answer.data !== undefined) {
		return answer.data;
	}
	if (answer.error !== undefined) {
		throw asError(answer.error, answer.response);
	}
	throw new Error("udl gave no answer");
}

/** udl's error as an `Error` the page can show: its message and hint, or what it did answer. */
export function asError(error: unknown, response?: Response): Error {
	if (isSaid(error)) {
		const said =
			typeof error.hint === "string" ? `${error.message} (${error.hint})` : error.message;
		return new Error(said);
	}
	return new Error(`udl answered ${response?.status ?? 0}: ${JSON.stringify(error)}`);
}

/** Whether `value` is udl's error, as it sends one: a message, and a hint when it has one. */
export function isSaid(value: unknown): value is { message: string; hint?: string | null } {
	if (typeof value !== "object" || value === null || !("message" in value)) {
		return false;
	}
	if (typeof value.message !== "string") {
		return false;
	}
	if (!("hint" in value)) {
		return true;
	}
	return value.hint === null || value.hint === undefined || typeof value.hint === "string";
}
