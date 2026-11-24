import { expect, type Page, test } from "@playwright/test";

/**
 * 29.2's proof, in a real browser: `web/plugins/orch` is the orchestrator's own UI plugin,
 * built by Vite into `src/udl/plugins/apps/orch/ui` (the folder *is* the bundle, so the orch app
 * plugin serves it with itself). Every route its manifest contributes draws a page, in the
 * plugin's own shadow root; its nav is in the shell's drawer; and its widget is on the dashboard.
 * (29.5's task page and 29.7's Q&A pages draw their own headings; a question's thread is 29.7's,
 * and reads the question — with nothing seeded here it says so, under the Q&A heading.)
 */

/** The routes the manifest contributes, and the heading each draws. */
const PAGES: [string, string][] = [
	["inbox", "Inbox"],
	["board", "Board"],
	["progress", "Progress"],
	["agents", "Agents"],
	["questions", "Q&A"],
	["merge", "Ready to merge"],
	["actions", "To action"],
	["features/29", "Feature 29"],
	["tasks/29.2", "Task 29.2"],
	["agents/claude", "claude"],
	["questions/Q-8", "Q&A"],
];

/** The nav the manifest contributes, in the order it contributes it (Q-8: Progress after Board). */
const NAV = ["Inbox", "Board", "Progress", "Agents", "Q&A", "Merge", "To action"];

/** What the plugin's page says it is: the first heading of its shadow root. */
async function headingOf(page: Page): Promise<string> {
	const slot = page.locator(".udl-plugin-slot");
	return slot.evaluate((host) => host.shadowRoot?.querySelector("h1")?.textContent ?? "");
}

/** The headings the shell's own DOM has: what a shadow root keeps out of `document`. */
async function shellHeadings(page: Page): Promise<string[]> {
	return page.evaluate(() =>
		[...document.querySelectorAll("h1")].map((one) => (one.textContent ?? "").trim()),
	);
}

test("every page the manifest contributes draws its heading, in the plugin's shadow root", async ({
	page,
}) => {
	for (const [route, heading] of PAGES) {
		await page.goto(`/ui/p/orch/${route}`);

		await expect(page.locator(".udl-plugin-slot")).toHaveCount(1);
		await expect.poll(async () => headingOf(page), { timeout: 15_000 }).toBe(heading);
		// (Isolated: the heading is in the plugin's shadow root, and not in the shell's own DOM.
		// `querySelectorAll` does not pierce a shadow root, so a page drawn outside one fails
		// here — which is the card's "inside the shell, isolated in its shadow root".)
		expect(await shellHeadings(page)).not.toContain(heading);
	}
});

test("its nav is in the drawer, Progress after Board (Q-8)", async ({ page }) => {
	await page.goto("/ui/");

	const drawer = page.locator(".drawer-side");
	for (const title of NAV) {
		await expect(drawer.getByRole("link", { name: title, exact: true })).toBeVisible();
	}

	const titles = await drawer.locator("li > a").allTextContents();
	const named = titles.map((one) => one.trim());
	const board = named.indexOf("Board");
	expect(board).toBeGreaterThanOrEqual(0);
	expect(named[board + 1]).toBe("Progress");
});

test("its widget is on the dashboard, counting what waits on a person", async ({ page }) => {
	await page.goto("/ui/");

	const slots = page.locator(".udl-plugin-slot");
	await expect
		.poll(
			async () => {
				const found = await slots.evaluateAll((hosts) =>
					hosts.map((host) => host.shadowRoot?.textContent ?? ""),
				);
				return found.some((text) => text.includes("Orchestrator"));
			},
			{ timeout: 15_000 },
		)
		.toBe(true);
});
