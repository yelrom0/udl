import { expect, test } from "@playwright/test";

/**
 * 24.8's proof: a UI plugin guides the page udl generates for another plugin. The shadcn example
 * (18.10) draws one of the fake's operation results with its own components (24.7's `resultViews`),
 * and everything it does not touch on that page — the settings form, the accounts, the destructive
 * operation and its confirm field — stays generated.
 *
 * The template plugin (18.10) guides the same page too: it hides `fake_ping` and draws
 * `fake_count_things`, which `generated.spec.ts` covers.
 */

test("a UI plugin draws one result, and the rest of the page stays generated", async ({
	page,
}, info) => {
	// (The two projects share one server and its database: each connects an account of its own.)
	const name = `guided-${info.project.name}`;
	await page.goto("/ui/p/fake/");
	await expect(page.getByRole("heading", { level: 2, name: "Fake" })).toBeVisible();

	// -- an account, connected with tokens ------------------------------------------------
	await page.getByRole("tab", { name: "accounts" }).click();
	await page.getByRole("textbox", { name: "Name", exact: true }).fill(name);
	await page.getByRole("checkbox", { name: "write", exact: true }).check();
	// (29.9: udl says which tokens an account takes, so the form draws their own fields —
	// labelled from the schema — rather than a name and a value typed by hand.)
	await page.getByLabel(/Api Key/).fill("e2e-fake-key");
	await page.getByRole("button", { name: "Connect", exact: true }).click();
	await expect(page.getByText(`fake:${name}`)).toBeVisible();

	// -- the guided result: shadcn's card, not udl's table --------------------------------
	await page.getByRole("tab", { name: "operations" }).click();
	const list = page.locator("article", { hasText: "List the things" });
	await list.getByRole("textbox", { name: /^Account/ }).fill(JSON.stringify(`fake:${name}`));
	await list.getByRole("button", { name: "Refresh" }).click();

	await expect(list.locator("[data-testid='shadcn-list']")).toBeVisible();
	await expect(list.getByText("The first thing")).toBeVisible();
	await expect(list.getByText("Drawn by shadcn/ui, not by udl.")).toBeVisible();
	// (udl's own drawing of a list is its table: it is not there.)
	await expect(list.locator("table")).toHaveCount(0);

	// -- the rest of the page: still udl's ------------------------------------------------
	const deleteCard = page.locator("article", { hasText: "Delete a thing" });
	await expect(deleteCard.getByRole("textbox", { name: "Confirm", exact: true })).toBeVisible();
	await expect(deleteCard.getByRole("button", { name: "Run" })).toBeDisabled();

	// (And the other plugin that guides this page still does: the template draws the count.)
	const countCard = page.locator("article", { hasText: "Count the things" });
	await countCard.getByRole("textbox", { name: /^account/i }).fill(`fake:${name}`);
	await countCard.getByRole("button", { name: "Refresh" }).click();
	await expect(countCard.getByText("Count the things, drawn by this plugin")).toBeVisible();
});
