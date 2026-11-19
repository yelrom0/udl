import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

// The nav's every item, in both themes (the config's two projects), down to a phone's width:
// the page each one names is reachable, and the theme can be switched and is remembered.
const PAGES = [
	{ label: "Dashboard", heading: "Dashboard" },
	{ label: "Downloads", heading: "Downloads" },
	{ label: "Sites", heading: "Sites" },
	{ label: "Chains", heading: "Chains" },
	{ label: "Events", heading: "Events" },
	{ label: "Subscriptions", heading: "Subscriptions" },
	{ label: "Actions", heading: "Actions" },
	{ label: "Runs", heading: "Runs" },
	{ label: "Schedules", heading: "Schedules" },
	{ label: "AI", heading: "AI" },
	{ label: "Accounts", heading: "Accounts" },
	{ label: "Plugins", heading: "Plugins" },
	{ label: "Databases", heading: "Databases" },
	{ label: "Settings", heading: "Settings" },
];

test("every page of the nav is reachable", async ({ page }) => {
	await signIn(page);

	for (const { label, heading } of PAGES) {
		await page.getByRole("link", { name: label, exact: true }).first().click();
		await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
	}
});

test("the nav is reachable at a phone's width", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await signIn(page);

	// (The nav is behind the menu button until it is opened.)
	await page.getByRole("button", { name: "Menu" }).click();
	const nav = page.locator("#nav");
	await expect(nav).toBeChecked();
	await page.getByRole("link", { name: "Sites", exact: true }).first().click();

	await expect(page.getByRole("heading", { level: 1, name: "Sites" })).toBeVisible();

	// (And choosing a page closes it: the nav must not cover the page it opened.)
	await expect(nav).not.toBeChecked();
	await expect(page.locator(".drawer-overlay")).toBeHidden();
});

test("the theme is switched, and stays switched", async ({ page }) => {
	await signIn(page);

	await page.getByRole("combobox", { name: "Theme" }).selectOption("dark");
	await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

	await page.getByRole("combobox", { name: "Theme" }).selectOption("light");
	await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

	// (The choice is the browser's to keep: it survives a new page of the same site.)
	await page.reload();
	await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
	await expect(page.getByRole("combobox", { name: "Theme" })).toHaveValue("light");
});

test("signing out goes back to the login page, and the API asks again", async ({ page }) => {
	await signIn(page);

	await page.getByRole("button", { name: "Sign out" }).click();

	await expect(page.getByRole("heading", { level: 1, name: "udl" })).toBeVisible();
	await expect(page.getByLabel("API key")).toBeVisible();
	await expect(page.getByRole("button", { name: "Sign out" })).toHaveCount(0);

	const answer = await page.request.get("/sites");
	expect(answer.status()).toBe(401);
});

test("a page that is not signed in goes to the login page", async ({ page }) => {
	await page.goto("/ui/downloads");

	await expect(page.getByLabel("API key")).toBeVisible();
});
