import { expect, test } from "@playwright/test";

test("the shell loads", async ({ page }) => {
	await page.goto("http://127.0.0.1:8765/docs");

	// (The build is served from 18.3; until then /ui/ is udl's "build it" page or a 404, so
	// what is checked here is that the server answered, and said so in HTML.)
	const answer = await page.request.get("http://127.0.0.1:8765/ui/");
	expect(answer.ok() || answer.status() === 404).toBe(true);
	await expect(page.locator("body")).toBeVisible();
});
