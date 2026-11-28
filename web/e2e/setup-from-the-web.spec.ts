import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { SIGNED_IN } from "./helpers";

/**
 * 29.9's Done when, proved: a person sets udl up **from the web alone** — the pages udl generates
 * for its own plugins, typed into, never the API from here — and each thing is read back as set
 * up. The GitHub is the fake `tests/support/e2e.py` serves (`support.github.FakeGitHub`): the
 * account's api url points at it, so connecting one checks its token against the fake and nothing
 * goes near github.com.
 *
 * The one leg that cannot be here is the OpenRouter key: there is no operation that sets an AI
 * provider until 17.9's `set_ai_provider`, so it is 17.9's Done when, not 29.9's.
 */

test.use({ storageState: SIGNED_IN });

/** What this run made, so it is undone even when a leg fails: a leftover project and host break
 * every spec after this one, and the failure that left them is the one worth reading. */
interface Made {
	readonly account: string;
	host: boolean;
	project: boolean;
	connected: boolean;
}

let made: Made | null = null;

// (In `afterEach`, not at the end of the body: a failure in the light project must not leave a
// project and a host behind for the dark one. Soft, so the cleanup never hides the first error.)
test.afterEach(async ({ page }) => {
	const mine = made;
	made = null;
	if (mine === null) {
		return;
	}
	const headers = { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin };
	if (mine.host) {
		const forgot = await page.request.delete("/hosts/e2e-host", { headers });
		expect.soft(forgot.ok(), `delete host answered ${forgot.status()}`).toBe(true);
		expect.soft(((await forgot.json()) as { deleted: boolean }).deleted).toBe(true);
	}
	if (mine.project) {
		const dropped = await page.request.post("/plugins/orch/delete_project", {
			headers,
			data: { name: "web" },
		});
		expect.soft(dropped.ok(), await dropped.text()).toBe(true);
		expect.soft(((await dropped.json()) as { deleted: boolean }).deleted).toBe(true);
	}
	if (mine.connected) {
		const unlinked = await page.request.delete(`/accounts/github:${mine.account}`, { headers });
		expect.soft(unlinked.ok(), `disconnect answered ${unlinked.status()}`).toBe(true);
		expect.soft(((await unlinked.json()) as { deleted: boolean }).deleted).toBe(true);
	}
});

/** The fake GitHub the harness serves: its address, its token and its repository (the harness
 * writes them, since a token never goes in a command line). Read inside the test, not at load:
 * Playwright lists the tests before `webServer` has written it. */
function harness(): { url: string; token: string; repo: string } {
	const path = process.env.UDL_E2E_GITHUB;
	if (path === undefined) {
		throw new Error("the harness did not say where it wrote the fake GitHub's address");
	}
	return JSON.parse(readFileSync(path, "utf8")) as { url: string; token: string; repo: string };
}

/** A read-back: udl's own answer, which is all this test asks of the API. */
async function read(page: Page, path: string): Promise<Record<string, unknown>> {
	const answer = await page.request.get(path);
	expect(answer.ok(), `${path} answered ${answer.status()}: ${await answer.text()}`).toBe(true);
	return (await answer.json()) as Record<string, unknown>;
}

/** One of the generated page's operation cards, by its title. */
function card(page: Page, title: string) {
	return page.locator("article").filter({ has: page.getByRole("heading", { name: title }) });
}

test("a person sets udl up from the web alone, and each thing is there", async ({ page }) => {
	const github = harness();
	const token = `ops_e2e_${String(Date.now())}`;
	// (A name of its own per run: both themes answer from one server, so a fixed name would find
	// the other theme's account already there rather than connecting one.)
	const account = `e2e-${String(Date.now())}`;
	made = { account, host: false, project: false, connected: false };

	// -- the 1Password token: udl's own page, its Secrets group ---------------------------
	await page.goto("/ui/p/udl/");
	await page.getByRole("tab", { name: "operations" }).click();
	const secrets = card(page, "Sign in to 1Password");
	await secrets.getByLabel(/Service account token/).fill(token);
	await secrets.getByRole("button", { name: "Run" }).click();
	// (It is kept, and udl's answer never carries it back: the answer is the status, a table of
	// the store and the resolvers.)
	await expect(secrets.getByRole("heading", { name: "Resolvers" })).toBeVisible({
		timeout: 30_000,
	});
	const kept = await read(page, "/secrets");
	// (The harness writes a file store, so an account connected before a restart is still
	// connected after it: udl reports the store it was configured with, and says a token is kept,
	// never which.)
	expect(kept.store).toBe("file");
	expect(kept.onepassword).toBe(true);
	expect(JSON.stringify(kept)).not.toContain(token);
	// (Nowhere on the page, and nowhere in its address.)
	expect(await page.locator("body").innerText()).not.toContain(token);
	expect(page.url()).not.toContain(token);

	// -- a host ---------------------------------------------------------------------------
	const hosts = card(page, "Add or change a host");
	// (Its fields are the host's own: the name, how udl reaches it, where worktrees go.)
	await hosts.getByRole("textbox", { name: "name" }).fill("e2e-host");
	await hosts.getByRole("combobox", { name: "HostTransport" }).selectOption("ssh");
	await hosts.getByRole("textbox", { name: "ssh" }).fill("e2e-host");
	await hosts.getByRole("textbox", { name: "Work Dir" }).fill("/tmp/e2e-worktrees");
	await hosts.getByRole("button", { name: "Run" }).click();
	// (Polled: the row lands when udl has it, not on the click.)
	const hostNames = async (): Promise<readonly string[]> => {
		const listed = await read(page, "/hosts");
		return (listed.hosts as readonly { name: string }[]).map((one) => one.name);
	};
	await expect.poll(hostNames, { timeout: 30_000 }).toContain("e2e-host");
	if (made !== null) {
		made.host = true;
	}

	// -- a GitHub account, against the fake ------------------------------------------------
	await page.goto("/ui/p/github/");
	await page.getByRole("tab", { name: "accounts" }).click();
	const connect = page.locator("form").filter({ hasText: "Connect an account" });
	await connect.getByLabel(/^Name/).fill(account);
	await connect.getByLabel(/^Token/).fill(github.token);
	await connect.getByLabel(/Repositories/).fill(github.repo);
	// (Where GitHub's API is: the harness's fake, never github.com.)
	await connect.getByLabel(/api url/i).fill(github.url);
	await connect.getByRole("button", { name: "Connect" }).click();
	// (It says connected, and udl's own list of accounts says so too. Polled: connecting checks
	// the token against the fake first, so the row lands when udl has answered, not on the click.)
	const connected = async (): Promise<readonly string[]> => {
		const answer = await page.request.get("/accounts");
		expect(answer.ok()).toBe(true);
		const said = (await answer.json()) as { accounts: readonly { name: string }[] };
		return said.accounts.map((one) => one.name);
	};
	await expect.poll(connected, { timeout: 30_000 }).toContain(account);
	if (made !== null) {
		made.connected = true;
	}
	await expect(page.getByRole("row", { name: new RegExp(account) })).toContainText("Connected");

	// -- a project with a repo, on the orchestrator's page ----------------------------------
	await page.goto("/ui/p/orch/");
	await page.getByRole("tab", { name: "operations" }).click();
	const project = card(page, "Create or edit a project");
	await project.getByRole("textbox", { name: "Project" }).fill("web");
	await project.getByRole("textbox", { name: "title" }).fill("The web");
	await project.getByRole("button", { name: "Run" }).click();
	// (The repo names the project, so the project's write must land first: a repo that arrives
	// before it fails with "no project web", and the poll below would only time out.)
	const projectExists = async (): Promise<boolean> => {
		const answer = await page.request.post("/plugins/orch/get_project", {
			headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
			data: { name: "web" },
		});
		return answer.ok();
	};
	await expect.poll(projectExists, { timeout: 30_000 }).toBe(true);
	if (made !== null) {
		made.project = true;
	}

	const repo = card(page, "Create or edit a repo");
	await repo.getByRole("textbox", { name: "project" }).fill("web");
	await repo.getByRole("textbox", { name: "Repo" }).fill("web");
	await repo.getByRole("textbox", { name: "On GitHub" }).fill(github.repo);
	await repo.getByRole("textbox", { name: "account" }).fill(`github:${account}`);
	await repo.getByRole("button", { name: "Run" }).click();

	// (The page's write lands when udl has it, not when the button was clicked: the project's own
	// read is polled until the repo is on it.)
	const reposOnTheProject = async (): Promise<readonly string[]> => {
		// (A write udl refused says why on the card: that is the failure to report, not a wait
		// that times out 30 s later.)
		expect((await repo.getByRole("alert").allInnerTexts()).join(" ")).toBe("");
		const answer = await page.request.post("/plugins/orch/get_project", {
			headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
			data: { name: "web" },
		});
		expect(answer.ok(), await answer.text()).toBe(true);
		const said = (await answer.json()) as { repos?: readonly { github: string }[] };
		return (said.repos ?? []).map((one) => one.github);
	};
	await expect.poll(reposOnTheProject, { timeout: 30_000 }).toContain(github.repo);
});
