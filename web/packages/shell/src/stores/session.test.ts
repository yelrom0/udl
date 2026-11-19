import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { check, SignInError, session, signIn, signOut, useSession } from "./session";

/** An answer as `fetch` gives one: a status and a JSON body. */
function answer(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

function signingInWith(...answers: Response[]): void {
	vi.stubGlobal(
		"fetch",
		vi.fn(() => Promise.resolve(answers.shift() ?? new Response(null, { status: 204 }))),
	);
}

beforeEach(() => {
	session.setState({ state: "unknown" });
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe("the session store", () => {
	it("starts not knowing who the page is", () => {
		expect(session.getState().state).toBe("unknown");
	});

	it("is signed in when the API answers", async () => {
		vi.spyOn(globalThis, "fetch").mockResolvedValue(answer(200, { downloads: [], total: 0 }));

		await expect(check()).resolves.toBe("signed-in");
		expect(session.getState().state).toBe("signed-in");
	});

	it("is signed out when the API asks for the key", async () => {
		vi.spyOn(globalThis, "fetch").mockResolvedValue(
			answer(401, { code: "unauthorized", message: "This udl server needs its API key" }),
		);

		await expect(check()).resolves.toBe("signed-out");
		expect(session.getState().state).toBe("signed-out");
	});

	it("is signed in when udl itself failed, since the session is not what failed", async () => {
		vi.spyOn(globalThis, "fetch").mockResolvedValue(answer(500, { message: "udl failed" }));

		await expect(check()).resolves.toBe("signed-in");
	});

	it("signs in with the key, and the cookie udl answers with is the session", async () => {
		signingInWith(new Response(null, { status: 204 }));

		await signIn("s3cret");

		expect(session.getState().state).toBe("signed-in");
		const [url, sent] = vi.mocked(globalThis.fetch).mock.calls[0] ?? [];
		expect(url).toBe("/session");
		expect(sent?.method).toBe("POST");
		expect(sent?.body).toBe(JSON.stringify({ api_key: "s3cret" }));
	});

	it("says what udl said about a wrong key, with its hint", async () => {
		signingInWith(
			answer(401, {
				code: "unauthorized",
				message: "This udl server needs its API key",
				hint: "The key is api.api_key in the server's config.",
			}),
		);

		const failed = await signIn("wrong").catch((error: unknown) => error);

		expect(failed).toBeInstanceOf(SignInError);
		expect((failed as SignInError).status).toBe(401);
		expect((failed as SignInError).message).toBe(
			"This udl server needs its API key (The key is api.api_key in the server's config.)",
		);
		expect(session.getState().state).toBe("unknown");
	});

	it("takes udl's message without a code as udl's own error", async () => {
		signingInWith(answer(400, { message: "The body is not a sign-in" }));

		const failed = await signIn("x").catch((error: unknown) => error);

		expect((failed as SignInError).info).toEqual({
			code: "udl_error",
			message: "The body is not a sign-in",
		});
	});

	it("says a key was not accepted when the answer held no message", async () => {
		signingInWith(answer(401, { detail: [{ msg: "no" }] }));

		const failed = await signIn("wrong").catch((error: unknown) => error);

		expect((failed as SignInError).info).toBeNull();
		expect((failed as SignInError).message).toBe("The API key was not accepted.");
	});

	it("says a key was not accepted when the answer was not udl's error", async () => {
		signingInWith(new Response("nope", { status: 401 }));

		const failed = await signIn("wrong").catch((error: unknown) => error);

		expect((failed as SignInError).message).toBe("The API key was not accepted.");
	});

	it("says too many tries when udl slowed them down", async () => {
		signingInWith(new Response("slow down", { status: 429 }));

		const failed = await signIn("wrong").catch((error: unknown) => error);

		expect((failed as SignInError).message).toBe(
			"Too many sign-in attempts: try again in a minute.",
		);
	});

	it("signs out, which udl answers by dropping the session", async () => {
		session.setState({ state: "signed-in" });
		signingInWith(new Response(null, { status: 204 }));

		await signOut();

		expect(session.getState().state).toBe("signed-out");
		const [, sent] = vi.mocked(globalThis.fetch).mock.calls[0] ?? [];
		expect(sent?.method).toBe("DELETE");
	});

	it("hands the state to a component", async () => {
		const seen: string[] = [];
		const unsubscribe = session.subscribe((state) => seen.push(state.state));

		session.setState({ state: "signed-in" });
		unsubscribe();

		expect(seen).toEqual(["signed-in"]);
		expect(useSession).toBeTypeOf("function");
	});
});
