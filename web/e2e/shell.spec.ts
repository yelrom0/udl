import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

// The card's own check: `/ui/` loads in both themes, from the build `make web` makes (which
// `make e2e` runs first). The server has an API key, so the page is signed in first.
test("the shell loads", async ({ page }) => {
	await signIn(page);

	await expect(page.getByRole("link", { name: "udl" })).toBeVisible();
	await expect(page.locator("html")).toHaveAttribute("data-theme", /light|dark/);
});

// What the harness is for today: a real `udl serve`, answering a browser.
test("the server answers the API", async ({ page }) => {
	const answer = await page.request.get("/docs");

	expect(answer.ok()).toBe(true);
});
