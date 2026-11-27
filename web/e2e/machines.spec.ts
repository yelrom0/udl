import { writeFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { SIGNED_IN } from "./helpers";

/**
 * 29.9's two generated-page views, in a real browser: the Machines group draws a card per host
 * (not a list's rows), and an import draws what it made of the plan files — the keys it created
 * and the line it could not place — rather than a wall of JSON.
 */

test.use({ storageState: SIGNED_IN });

/** One of the plugin's operations, through the API, the way the page calls it. */
async function call(page: Page, name: string, body: unknown): Promise<void> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		data: body,
		// (A write with a session's cookie says where it comes from, as a browser's own does.)
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
	});
	expect(answer.ok(), `${name}: ${await answer.text()}`).toBe(true);
}

test("the machines are a card per host, and an import says what it made of the plan", async ({
	page,
}) => {
	// (A page first: a write says where it comes from, and that is the server's own address.)
	await page.goto("/ui/");
	// (The one project the e2e server's other specs seed too: the import leaves it out.)
	await call(page, "set_project", { name: "udl", title: "udl" });

	await page.goto("/ui/p/orch/");
	await page.getByRole("tab", { name: "operations" }).click();

	// -- the machines, drawn by the plugin rather than as a list's rows ---------------------
	const machines = page.locator("[data-machines]");
	await expect(machines).toBeVisible();
	await expect(machines.getByRole("heading", { name: "Machines" })).toBeVisible();
	// (One per host udl knows, with the harnesses it can see there: none, or the ones installed.)
	await expect(machines.getByText(/\d+ hosts?/)).toBeVisible();
	await expect(machines.getByRole("button", { name: "Refresh" })).toBeVisible();

	// -- an import's report, from a plan file the page is pointed at ------------------------
	// (Playwright's own output folder, which it clears up after the run.)
	const plan = test.info().outputPath("plan.md");
	writeFileSync(
		plan,
		[
			"# A plan",
			"",
			"## Progress tracker",
			"",
			"- [ ] **Phase 30: A phase**",
			"",
			"  - [ ] 30.1 a task",
			"  - [ ] not a task at all",
			"",
		].join("\n"),
	);

	const imported = page.locator("article", { hasText: "Read plan files into udl" });
	await imported.getByLabel(/^Plan files/).fill(plan);
	await imported.getByLabel(/Only report what it would do/).check();
	await imported.getByRole("button", { name: "Run" }).click();

	// (What it would make, by key, and the one line it could not place.)
	const report = page.locator("[data-import-report]");
	await expect(report).toBeVisible({ timeout: 30_000 });
	await expect(report.getByText("Created 2")).toBeVisible();
	await expect(report.getByText("Not placed 1")).toBeVisible();
	await expect(report.getByText(/not a task at all/)).toBeVisible();
});
