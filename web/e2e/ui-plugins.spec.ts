import { expect, test } from "@playwright/test";

/**
 * 18.9's plugin host, in a real browser: udl serves two UI plugins from
 * `tests/support/e2e_ui_plugins` — `hello` (a page, a dashboard widget and a stylesheet, all
 * plain ES modules) and `boom` (a page that throws). The shell mounts them in shadow roots of
 * their own, and a plugin that fails is a card in its place, not the end of the page.
 */

test("a plugin's nav item is in the drawer, and its page draws in a shadow root", async ({
	page,
}) => {
	await page.goto("/ui/");

	const item = page.getByRole("link", { name: "Hello" });
	await expect(item).toBeVisible();
	await item.click();

	await expect(page).toHaveURL(/\/ui\/p\/hello\/hello$/);
	const slot = page.locator(".udl-plugin-slot");
	await expect(slot).toHaveCount(1);
	// (Its own root: the shell's DOM does not have its text, the slot's shadow root does.)
	await expect(page.getByText("Hello from the plugin, on hello")).toHaveCount(0);
	await expect
		.poll(async () => slot.evaluate((host) => host.shadowRoot?.textContent ?? ""))
		.toContain("Hello from the plugin, on hello");
});

test("a plugin's stylesheet is adopted, its @property hoisted, and its theme the page's", async ({
	page,
}) => {
	await page.goto("/ui/p/hello/hello");
	const theme = await page.evaluate(() => document.documentElement.dataset.theme);

	const slot = page.locator(".udl-plugin-slot");
	await expect
		.poll(async () =>
			slot.evaluate((host) => {
				const root = host.shadowRoot;
				const drawn = root?.querySelector(".hello");
				return [
					drawn === null || drawn === undefined ? "" : getComputedStyle(drawn).color,
					root?.querySelector(".udl-plugin")?.getAttribute("data-theme") ?? "",
					// (A shadow root cannot register a custom property: the shell hoists the
					// plugin's `@property` rules to a sheet of the document.)
					String(
						document.adoptedStyleSheets.some((sheet) =>
							[...sheet.cssRules].some((rule) => rule.cssText.startsWith("@property")),
						),
					),
				].join("|");
			}),
		)
		.toBe(`rgb(1, 2, 3)|${theme}|true`);
});

test("a plugin's widget is on the dashboard", async ({ page }) => {
	await page.goto("/ui/");

	const widgets = page.locator(".udl-plugin-slot");
	await expect(widgets).toHaveCount(1);
	await expect
		.poll(async () => widgets.first().evaluate((host) => host.shadowRoot?.textContent ?? ""))
		.toContain("Hello from the plugin, on dashboard");
});

test("a plugin that throws shows an error card, and the shell carries on", async ({ page }) => {
	await page.goto("/ui/p/boom/throw");

	const card = page.getByRole("alert");
	await expect(card).toBeVisible();
	await expect(card).toContainText("The boom UI failed");
	await expect(card).toContainText("boom throws on purpose");

	// (The shell is still the shell: its drawer, its pages, its own dashboard.)
	await page.getByRole("link", { name: "Dashboard" }).click();
	await expect(page).toHaveURL(/\/ui\/$/);
	await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
});

test("a plugin page that does not exist says so", async ({ page }) => {
	await page.goto("/ui/p/hello/nothing");

	await expect(page.getByRole("heading", { name: "hello 0.1.0" })).toBeVisible();
	await expect(page.getByText(/has no page "nothing"/)).toBeVisible();
});
