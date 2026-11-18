import { expect, test } from "@playwright/test";

// The card's own check: `/ui/` loads in both themes. It is served from 18.3, so until then it
// is `test.fixme` — a check that passes however `/ui/` answers would be worse than none.
test("the shell loads", async ({ page }) => {
	test.fixme(true, "the built shell is served from 18.3");

	await page.goto("/ui/");
	await expect(page.getByRole("heading", { name: "udl", level: 1 })).toBeVisible();
	await expect(page.locator("html")).toHaveAttribute("data-theme", /light|dark/);
});

// What the harness is for today: a real `udl serve`, answering a browser.
test("the server answers the API", async ({ page }) => {
	const answer = await page.request.get("/docs");

	expect(answer.ok()).toBe(true);
});