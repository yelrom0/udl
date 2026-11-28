/**
 * The e2e run's one sign-in: udl answers `POST /session` with a cookie, which Playwright keeps
 * and gives every test's browser. Signing in once is also what keeps the run inside udl's
 * sign-in limit (ten a minute from one address), so a test that signs in itself says why.
 *
 * A test that restarts udl (29.10's story does: the dispatcher reads its agents at start) finds
 * every session gone, because a session lives in the server's memory — so it signs in again and
 * rewrites this file, which every later test reads. That is why the projects' `storageState` is
 * this file and not a copy: the suite runs one spec at a time, so the file is always the one
 * that was written last.
 */

import { expect, test as setup } from "@playwright/test";
import { API_KEY, SIGNED_IN } from "./helpers";

setup("sign in", async ({ request }) => {
	const answer = await request.post("/session", { data: { api_key: API_KEY } });

	expect(answer.status()).toBe(204);
	await request.storageState({ path: SIGNED_IN });
});
