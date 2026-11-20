import { expect, test } from "@playwright/test";

/**
 * 24.6's generated page, in a real browser: the e2e config loads `tests/support/e2e_plugins/fake.py`,
 * an integration with no UI plugin of its own, so `/ui/p/fake/` is the page udl generates from
 * what the plugin declares — its settings, its accounts, its operations and its event types.
 *
 * The account is connected with tokens (the fake accepts any key but `refused`), the read-only
 * list runs on open, the destructive operation asks first and keeps its button disabled until
 * its `confirm` field says the thing's id, and a setting is changed from its form.
 */

test("a plugin with no UI plugin gets a page that does something", async ({ page }, info) => {
	// (The two projects share one server and its database: each connects an account of its own.)
	const name = `work-${info.project.name}`;
	await page.goto("/ui/plugins");

	// (The Plugins page links every plugin to its generated page.)
	// (The drawer has the plugin's page too: the list's own link is the one to click.)
	const link = page.getByRole("main").getByRole("link", { name: "Fake" });
	await expect(link).toBeVisible();
	await link.click();
	await expect(page).toHaveURL(/\/ui\/p\/fake\/?$/);
	await expect(page.getByRole("heading", { level: 2, name: "Fake" })).toBeVisible();

	// (Its tabs are the ones it has something for.)
	for (const tab of ["operations", "settings", "accounts", "events"]) {
		await expect(page.getByRole("tab", { name: tab })).toBeVisible();
	}

	// -- an account, connected with tokens ------------------------------------------------
	await page.getByRole("tab", { name: "accounts" }).click();
	await page.getByRole("textbox", { name: "Name", exact: true }).fill(name);
	// (What for: a read-only level would refuse the destructive operation.)
	await page.getByRole("checkbox", { name: "write", exact: true }).check();
	await page.getByLabel("Token name").fill("api_key");
	await page.getByRole("button", { name: "Add token" }).click();
	await page.getByRole("textbox", { name: "api_key" }).fill("e2e-fake-key");
	await page.getByRole("button", { name: "Connect", exact: true }).click();
	await expect(page.getByText(`fake:${name}`)).toBeVisible();

	// (Check, and udl says it works.)
	const row = page.locator("tr", { hasText: `fake:${name}` });
	await row.getByRole("button", { name: "Check" }).click();
	await expect(row.getByText("ok", { exact: true })).toBeVisible();

	// -- a read-only operation: its table ------------------------------------------------
	await page.getByRole("tab", { name: "operations" }).click();
	const listCard = page.locator("article", { hasText: "List the things" });
	// (It ran on open; with two accounts on the server it asks which, so name this project's —
	// the field takes one or many, so its box holds JSON.)
	await listCard.getByRole("textbox", { name: /^Account/ }).fill(JSON.stringify(`fake:${name}`));
	await listCard.getByRole("button", { name: "Refresh" }).click();
	await expect(listCard.getByText("The first thing")).toBeVisible();
	await expect(listCard.getByText("The second thing")).toBeVisible();

	// -- a destructive one: only after confirming -----------------------------------------
	const deleteCard = page.locator("article", { hasText: "Delete a thing" });
	const run = deleteCard.getByRole("button", { name: "Run" });
	await expect(run).toBeDisabled();
	// (Both projects share the server: the account is named, not left to the one there is.)
	await deleteCard.getByRole("textbox", { name: /^account/ }).fill(`fake:${name}`);
	await deleteCard.getByRole("textbox", { name: "Name", exact: true }).fill("t1");
	await deleteCard.getByRole("textbox", { name: "Confirm", exact: true }).fill("t");
	await expect(run).toBeDisabled();
	await deleteCard.getByRole("textbox", { name: "Confirm", exact: true }).fill("t1");
	await expect(run).toBeEnabled();

	await run.click();
	const dialog = page.getByRole("dialog");
	await expect(dialog).toContainText("Delete a thing t1?");
	// (Answered no: nothing happens.)
	await dialog.getByRole("button", { name: "Cancel" }).click();
	await expect(deleteCard.getByRole("alert")).toHaveCount(0);

	// (Answered yes: udl runs it, and the answer is drawn.)
	await run.click();
	await page.getByRole("dialog").getByRole("button", { name: "Delete a thing" }).click();
	await expect(deleteCard.getByText("t1", { exact: true })).toBeVisible();

	// -- a setting, changed from its form -------------------------------------------------
	await page.getByRole("tab", { name: "settings" }).click();
	const label = page.getByLabel(/^Label/);
	// (The secret is a password box, and the page never shows what it holds.)
	await expect(page.getByLabel(/^Api Key/)).toHaveAttribute("type", "password");
	await label.fill(`the ${info.project.name} fake`);
	await page.getByRole("button", { name: "Save" }).click();
	await expect(page.getByText("Saved", { exact: true })).toBeVisible();

	// (And it stayed saved.)
	await page.reload();
	await page.getByRole("tab", { name: "settings" }).click();
	await expect(page.getByLabel(/^Label/)).toHaveValue(`the ${info.project.name} fake`);

	// -- its event types ------------------------------------------------------------------
	await page.getByRole("tab", { name: "events" }).click();
	await expect(page.getByText("fake.thing.happened")).toBeVisible();
	await expect(page.getByText("A thing happened in the fake.")).toBeVisible();
});

test("another plugin's manifest guides the page (24.7)", async ({ page }, info) => {
	// (`web/packages/template`'s manifest hides the fake's `ping` and draws its `count_things`
	// itself: guidance names operations, and the page it guides is udl's.)
	const name = `work-${info.project.name}`;
	await page.goto("/ui/p/fake/");
	await page.getByRole("tab", { name: "operations" }).click();

	// -- the operation its guidance hides is not drawn ---------------------------------------
	const countCard = page.locator("article", { hasText: "Count the things" });
	await expect(countCard.getByRole("heading", { level: 4 })).toHaveText("Count the things");
	await expect(page.getByText("Ping", { exact: true })).toHaveCount(0);

	// -- the result it draws itself, in place of udl's table ---------------------------------
	const card = countCard;
	// (Not a fan-out list, so its account box takes one name, not JSON.)
	await card.getByRole("textbox", { name: /^account/i }).fill(`fake:${name}`);
	await card.getByRole("button", { name: "Refresh" }).click();

	await expect(card.getByText("Count the things, drawn by this plugin")).toBeVisible();
});
