import { expect, type Page, test } from "@playwright/test";

/**
 * 18.10's proof, in a real browser: `web/examples/shadcn` is a UI plugin written with React and
 * shadcn/ui, built by Vite with a Tailwind 4 setup of its own. udl serves it from
 * `web/examples/shadcn/dist` (a folder of bundles, as `make web` leaves it).
 *
 * What it proves: a plugin's own styles reach its shadow root, they are shadcn's tokens mapped
 * onto daisyUI's (so the shell's theme drives them), and none of it touches the shell's styles.
 */

/** The token, as a browser spells a computed colour (`oklch(45% .24 277)` → `oklch(0.45 …)`). */
async function colourOf(page: Page, name: string): Promise<string> {
	return page.evaluate(
		([token]) => {
			const probe = document.createElement("div");
			probe.style.backgroundColor = `var(${token})`;
			document.body.append(probe);
			const value = getComputedStyle(probe).backgroundColor;
			probe.remove();
			return value;
		},
		[name],
	);
}

/** What the plugin drew: one element of its shadow root, and a computed style (or attribute) of
 * it — the wrapper's `data-theme` included, since a document's query cannot reach a root's. */
async function drawn(page: Page, id: string, style: string): Promise<string> {
	return page.evaluate(
		([id, style]) => {
			const hosts = [...document.querySelectorAll(".udl-plugin-slot")];
			for (const host of hosts) {
				const found = host.shadowRoot?.querySelector(`[data-testid='${id}']`);
				if (found === null || found === undefined) {
					continue;
				}
				if (style === "text") {
					return found.textContent ?? "";
				}
				if (style === "data-theme") {
					return found.closest(".udl-plugin")?.getAttribute("data-theme") ?? "";
				}
				return getComputedStyle(found)[style as "backgroundColor" | "color"];
			}
			return "";
		},
		[id, style],
	);
}

/** The page's own styles: what the shell draws with, which the plugin must not change. */
async function shellStyles(page: Page): Promise<Record<string, string>> {
	return page.evaluate(() => {
		const body = getComputedStyle(document.body);
		const root = getComputedStyle(document.documentElement);
		return {
			colour: body.color,
			// (The page's ground is the root's; the body is transparent, as daisyUI leaves it.)
			background: root.backgroundColor,
			font: body.fontSize,
			// (A plugin's tokens are its own: its `--primary` is not the page's.)
			primary: root.getPropertyValue("--primary").trim(),
		};
	});
}

test("the shadcn/ui plugin's page draws with its own styles", async ({ page }) => {
	await page.goto("/ui/p/shadcn/gallery");

	// (Its own Tailwind build reached the shadow root, and shadcn's `--primary` is daisyUI's:
	// the button is the shell's primary colour, whatever that theme's is.)
	const primary = await colourOf(page, "--color-primary");
	expect(primary).not.toBe("rgba(0, 0, 0, 0)");
	await expect.poll(() => drawn(page, "shadcn-button", "backgroundColor")).toBe(primary);

	// (Its text is shadcn's card foreground, which is the theme's content colour, and its page
	// says which route it was drawn for.)
	await expect
		.poll(() => drawn(page, "shadcn-card", "color"))
		.toBe(await colourOf(page, "--color-base-content"));
	await expect.poll(() => drawn(page, "shadcn-page", "text")).toContain("gallery");
});

test("the plugin follows the theme switch, and leaves the shell's styles alone", async ({
	page,
}) => {
	// (The shell's own page first, before any of the plugin's CSS has been adopted. Both projects
	// run this test, so the theme it switches to is the other one of the two.)
	await page.goto("/ui/");
	const shellBefore = await shellStyles(page);
	const was = await page.locator("html").getAttribute("data-theme");
	const other = was === "dark" ? "light" : "dark";

	await page.goto("/ui/p/shadcn/gallery");
	const firstPrimary = await colourOf(page, "--color-primary");
	await expect.poll(() => drawn(page, "shadcn-button", "backgroundColor")).toBe(firstPrimary);

	// (The plugin's CSS never left its root: the shell's page is exactly what it was.)
	expect(await shellStyles(page)).toEqual(shellBefore);

	await page.getByRole("combobox", { name: "Theme" }).selectOption(other);
	await expect(page.locator("html")).toHaveAttribute("data-theme", other);

	// (The plugin's own wrapper follows the shell's theme, and so does what it draws: the other
	// palette's primary, not the one it had.)
	const otherPrimary = await colourOf(page, "--color-primary");
	expect(otherPrimary).not.toBe(firstPrimary);
	await expect.poll(() => drawn(page, "shadcn-button", "data-theme")).toBe(other);
	await expect.poll(() => drawn(page, "shadcn-button", "backgroundColor")).toBe(otherPrimary);

	// (Still the shell's own styles, now the other theme's: its text and its ground are daisyUI's,
	// and the plugin's `--primary` is still not the page's.)
	const shellAfter = await shellStyles(page);
	expect(shellAfter.primary).toBe("");
	expect(shellAfter.colour).toBe(await colourOf(page, "--color-base-content"));
	expect(shellAfter.background).toBe(await colourOf(page, "--color-base-100"));
	expect(shellAfter.font).toBe(shellBefore.font);
});

test("the plugin's widget is on the dashboard, with its own components", async ({ page }) => {
	await page.goto("/ui/");

	// (The dashboard's slot: the plugin's card, drawn by the same shadcn components.)
	await expect.poll(() => drawn(page, "shadcn-widget", "text")).toContain("shadcn/ui");
	await expect.poll(() => drawn(page, "shadcn-widget-button", "text")).toContain("dashboard");
});
