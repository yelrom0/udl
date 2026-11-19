/**
 * Who the page is. udl answers with no `api_key` set to anyone (localhost, the default); with
 * one, only to the session's cookie, which signing in with the key sets. So "who am I" is a
 * question only the API can answer: `check()` asks it something cheap, and a 401 means the
 * login page.
 */

import type { ErrorInfo } from "@udl/api";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { isSaid, onUnauthorized } from "../api";

export type SessionState = "unknown" | "signed-in" | "signed-out";

export const session = createStore<{ state: SessionState }>(() => ({ state: "unknown" }));

// (A session lives in udl's memory: after a restart or an expiry every request is a 401, and
// the page goes back to the login page rather than showing an error it cannot fix.)
onUnauthorized(() => session.setState({ state: "signed-out" }));

/** What udl says about a sign-in that was refused: its error, and the status it came with. */
export class SignInError extends Error {
	readonly info: ErrorInfo | null;
	readonly status: number;

	constructor(message: string, status: number, info: ErrorInfo | null) {
		super(message);
		this.name = "SignInError";
		this.info = info;
		this.status = status;
	}
}

/** Ask udl whether this page may read the API, and remember its answer. */
export async function check(): Promise<SessionState> {
	// (Any answer but a 401 is a signed-in page: a 500 is udl's, not the session's. A plain
	// `fetch` rather than the typed client: this is a probe, not data.)
	const response = await fetch("/sites?limit=1", { credentials: "same-origin" });
	const state: SessionState = response.status === 401 ? "signed-out" : "signed-in";
	session.setState({ state });
	return state;
}

/** Sign in with the API key the user typed (the cookie it answers with signs the page in). */
export async function signIn(apiKey: string): Promise<void> {
	const answer = await fetch("/session", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ api_key: apiKey }),
	});
	if (!answer.ok) {
		// (One read of the body: the message is made from the error it held.)
		const given = await info(answer);
		throw new SignInError(refusal(given, answer.status), answer.status, given);
	}
	session.setState({ state: "signed-in" });
}

/** Sign out: udl drops the session, and the page goes back to the login page. */
export async function signOut(): Promise<void> {
	await fetch("/session", { method: "DELETE" });
	session.setState({ state: "signed-out" });
}

/** What udl said a refused sign-in was (its own words when it answered with its error). */
function refusal(given: ErrorInfo | null, status: number): string {
	if (given) {
		return given.hint ? `${given.message} (${given.hint})` : given.message;
	}
	return status === 429
		? "Too many sign-in attempts: try again in a minute."
		: "The API key was not accepted.";
}

/** udl's error from an answer, when it sent one. */
async function info(answer: Response): Promise<ErrorInfo | null> {
	try {
		const body: unknown = await answer.json();
		if (isSaid(body)) {
			return {
				code: "udl_error",
				message: body.message,
				...(body.hint ? { hint: body.hint } : {}),
			};
		}
		return null;
	} catch {
		return null;
	}
}

/** The page's session state, as a hook. */
export const useSession = () => useStore(session, (state) => state.state);
