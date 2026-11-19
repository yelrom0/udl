import { expect, test } from "@playwright/test";

// The card's own check: `/ui/` loads in both themes, from the build `make web` makes (which
// `make e2e` runs first). The browser is signed in by `auth.setup.ts`.
test("the shell loads", async ({ page }) => {
	await page.goto("/ui/");

	await expect(page.getByRole("link", { name: "udl" })).toBeVisible();
	await expect(page.locator("html")).toHaveAttribute("data-theme", /light|dark/);
});

// 18.6: the page follows udl's events stream, in a real browser.
test("the page is live", async ({ page }) => {
	await page.goto("/ui/");

	await expect(
		page.getByText("udl is telling the page what happens, as it happens."),
	).toBeVisible();
});

// 18.6's "done when": something udl does shows up on the page, live — an event udl stores,
// sent from outside the browser, appears in the page's list without a reload.
test("an event shows up live", async ({ page }) => {
	await page.goto("/ui/");
	await expect(
		page.getByText("udl is telling the page what happens, as it happens."),
	).toBeVisible();

	const answer = await page.request.post("/events", {
		// (A write with the session's cookie needs the CSRF header and the page's own origin:
		// 18.4's rules, and Playwright's request is not the page.)
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: { type: "custom.hello", data: { subject: "The page is watching" } },
	});

	expect(answer.ok()).toBe(true);
	await expect(page.getByText("The page is watching")).toBeVisible();
});

// What the harness is for today: a real `udl serve`, answering a browser.
test("the server answers the API", async ({ page }) => {
	const answer = await page.request.get("/docs");

	expect(answer.ok()).toBe(true);
});
