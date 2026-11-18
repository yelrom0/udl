import { expect, type Page } from "@playwright/test";

/** The API key `tests/support/e2e.py` gives the server (its `API_KEY`). */
export const API_KEY = "e2e-key";

/** Sign in the way a user does: the key, on the login page, which answers with a session. */
export async function signIn(page: Page): Promise<void> {
	await page.goto("/ui/login");
	await page.getByLabel("API key").fill(API_KEY);
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();
}
