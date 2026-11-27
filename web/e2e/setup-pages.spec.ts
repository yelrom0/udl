import { expect, test } from "@playwright/test";

/**
 * 29.9's setup pages, in a real browser: the pages udl generates for its own plugins read as the
 * operations a person does them in — udl's hosts and secrets, and the orchestrator's projects,
 * repos, machines and import — each in a group, each field saying what goes in it.
 *
 * (The orchestrator's page is the one hand-built pages cannot cover: its day-to-day operations
 * have pages of their own, and its setup ones are the generated page's — `hide` and `show` in its
 * manifest. udl's own page has no UI plugin at all, so it is generated whole.)
 */

test("a plugin's setup operations read in groups, with fields that say what goes in them", async ({
	page,
}) => {
	await page.goto("/ui/plugins");

	// -- udl's own page: the hosts, then the secrets --------------------------------------
	await page.getByRole("main").getByRole("link", { name: "udl", exact: true }).click();
	await expect(page).toHaveURL(/\/ui\/p\/udl\/?$/);
	await page.getByRole("tab", { name: "operations" }).click();

	// (A group is the page's own heading: what a person does first, first.)
	const hosts = page.locator("section").filter({ hasText: "Add or change a host" });
	await expect(hosts.locator("> h3")).toHaveText("Hosts");
	await expect(hosts.getByRole("heading", { name: "Add or change a host" })).toBeVisible();
	const secrets = page.locator("section").filter({ hasText: "Sign in to 1Password" });
	await expect(secrets.locator("> h3")).toHaveText("Secrets");

	// (The host's own field says what it takes, and the changes one is a box of JSON.)
	const addHost = hosts.locator("article", { hasText: "Add or change a host" });
	await expect(addHost.getByLabel(/^Host/)).toBeVisible();
	await expect(addHost.getByText(/how udl reaches it/)).toBeVisible();

	// (The token is a password box that says what it is, and never draws one back.)
	const signIn = secrets.locator("article", { hasText: "Sign in to 1Password" });
	const token = signIn.getByLabel(/Service account token/);
	await expect(token).toHaveAttribute("type", "password");
	await expect(token).toHaveAttribute("placeholder", "ops_…");

	// -- the orchestrator's page: its setup operations, and only those ---------------------
	await page.goto("/ui/p/orch/");
	await page.getByRole("tab", { name: "operations" }).click();

	for (const group of ["Machines", "Projects", "Repos", "Import"]) {
		// (The group's own heading, not the one a card draws inside it.)
		await expect(page.locator("section > h3").filter({ hasText: group })).toHaveCount(1);
	}
	// (Its day-to-day operations have pages of their own: they are not here.)
	await expect(page.getByRole("heading", { name: "Assign a task" })).toHaveCount(0);
	await expect(page.locator("section > h3")).toHaveCount(4);

	// (A project's field is the project's name, and a repo says where it is on GitHub.)
	const projects = page.locator("section").filter({ hasText: "Create or edit a project" });
	await expect(
		projects.locator("article", { hasText: "Create or edit a project" }).getByLabel(/^Project/),
	).toBeVisible();
	const repos = page.locator("section").filter({ hasText: "Create or edit a repo" });
	const addRepo = repos.locator("article", { hasText: "Create or edit a repo" });
	await expect(addRepo.getByLabel(/On GitHub/)).toHaveAttribute("placeholder", "yelrom0/udl");
});
