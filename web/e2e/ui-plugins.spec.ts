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
	// (Its own root: the slot's shadow root has the plugin's text, and the page's own DOM —
	// which `textContent` leaves shadow roots out of, while a locator pierces them — has not.)
	await expect
		.poll(async () => slot.evaluate((host) => host.shadowRoot?.textContent ?? ""))
		.toContain("Hello from the plugin, on hello");
	expect(await page.evaluate(() => document.body.textContent ?? "")).not.toContain(
		"Hello from the plugin",
	);
});

test("a plugin's stylesheet is adopted, its @property hoisted, and its theme the page's", async ({
	page,
}) => {
	// (What the CSP blocks, as the page itself says it: nothing here may trip it.)
	await page.addInitScript(() => {
		document.addEventListener("securitypolicyviolation", (event) => {
			const body = document.body;
			if (body !== null) {
				body.dataset.cspViolations = `${body.dataset.cspViolations ?? ""}${event.violatedDirective} ${event.blockedURI}\n`;
			}
		});
	});
	const response = await page.goto("/ui/p/hello/hello");
	const headers = response === null ? {} : await response.allHeaders();
	const csp = headers["content-security-policy"] ?? "";
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

	// (Settled by 18.9: udl's CSP allows no inline style, and a plugin's stylesheet is not one —
	// it is adopted into the plugin's root, which the CSP does not govern.)
	expect(csp).toContain("default-src 'self'");
	expect(csp).not.toContain("unsafe-inline");
	expect(csp).not.toContain("unsafe-eval");
	expect(await page.evaluate(() => document.body.dataset.cspViolations ?? "")).toBe("");
});

test("a plugin's widget is on the dashboard", async ({ page }) => {
	await page.goto("/ui/");

	// (Four: this plugin's, 18.10's two — the shadcn/ui example and the template, which is loaded
	// the same way, so the starting point is proven in the shell it starts from — and the
	// orchestrator's own, 29.2's.)
	const widgets = page.locator(".udl-plugin-slot");
	await expect(widgets).toHaveCount(4);
	await expect
		.poll(async () =>
			widgets.evaluateAll((hosts) => hosts.map((host) => host.shadowRoot?.textContent ?? "")),
		)
		.toContainEqual(expect.stringContaining("Hello from the plugin, on dashboard"));
	await expect
		.poll(async () =>
			widgets.evaluateAll((hosts) => hosts.map((host) => host.shadowRoot?.textContent ?? "")),
		)
		.toContainEqual(expect.stringContaining("Drawn into the dashboard slot."));
	await expect
		.poll(async () =>
			widgets.evaluateAll((hosts) => hosts.map((host) => host.shadowRoot?.textContent ?? "")),
		)
		.toContainEqual(expect.stringContaining("Orchestrator"));
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
