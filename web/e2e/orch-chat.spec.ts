import { expect, type Page, test } from "@playwright/test";
import { SIGNED_IN } from "./helpers";

/**
 * 29.12's proof, in a real browser: the chat panel opens from the navbar, a person starts a chat
 * with a harness, completes a key with `@`, says something, answers what the harness asked,
 * reloads — the same chat is there — and pops it out into a window of its own, which is the same
 * conversation.
 *
 * The e2e server runs the real orchestrator plugin with 28.6's scripted runner (its turns are a
 * file this test writes through the harness's control API), so a turn is a tool call back into
 * udl's own MCP server: the harness asks before it acts, exactly as Claude Code does.
 *
 * (Ctrl+J is the same switch as the navbar's button, and the shell's own tests cover the key:
 * Chromium keeps Ctrl+J for its downloads and never gives it to the page.)
 */

test.use({ storageState: SIGNED_IN });

/** The chat panel: the page's copy, or the window it was popped out into. */
function panel(page: Page) {
	return page.locator('[data-panel="orch/chat"]');
}

/** One of the panel's own parts, by its accessible name (the labels are the chat's own, so the
 * page's lookup is unambiguous, and it reaches into the plugin's shadow root). */
function inside(page: Page, name: string) {
	return page.getByLabel(name).first();
}

/** One of the plugin's operations, through the API, the way a page calls it. */
async function call(page: Page, name: string, body: unknown): Promise<unknown> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		data: body,
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
	});
	expect(answer.ok(), `${name}: ${await answer.text()}`).toBe(true);
	return answer.json();
}

/** What the scripted harness does with the next turn it is given. */
async function script(turns: unknown[]): Promise<void> {
	const controls = process.env.UDL_E2E_CONTROLS;
	expect(controls, "the harness's control port (UDL_E2E_CONTROLS)").toBeTruthy();
	const answer = await fetch(`http://127.0.0.1:${controls}/turns`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(turns),
	});
	expect(answer.status, "the control API took the turns").toBe(204);
}

/** A project, a feature and a task, so `@` has keys to complete. */
async function seed(page: Page, run: string): Promise<void> {
	await page.goto("/ui/");
	const feature = `chat-${run}`;
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", { project: "udl", key: feature, title: "Chat" });
	await call(page, "set_task", {
		project: "udl",
		key: `${feature}.1`,
		feature,
		title: "The chat panel",
	});
}

/** Start a chat through the panel's own form, on the scripted harness. */
async function started(page: Page): Promise<void> {
	const form = inside(page, "A new chat");
	// (A panel draws the chat this browser last spoke in, when there is one, and the header's +
	// always asks for a new one — so this is the same two clicks whether or not there is one.)
	await expect(inside(page, "Start a new chat")).toBeVisible();
	await inside(page, "Start a new chat").click();
	await expect(form).toBeVisible();
	await inside(page, "Harness").selectOption("scripted");
	await inside(page, "Folder").fill("/tmp");
	await page.getByRole("button", { name: "Start the chat" }).click();
	await expect(inside(page, "Transcript")).toBeVisible();
}

test("the panel is a sheet the whole width of a phone", async ({ page }) => {
	// (A phone has no room beside the page: the panel is the window, from the top to the bottom.)
	await page.addInitScript(() => {
		localStorage.setItem("udl.panels", JSON.stringify({ open: ["orch/chat"], width: 400 }));
	});
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/ui/");

	const drawn = panel(page);
	await expect(drawn).toBeVisible();
	const box = await drawn.boundingBox();
	expect(box?.width).toBe(390);
	expect(box?.height).toBe(844);
});

test("a person chats, answers what it asks, and keeps the chat across a reload and a window", async ({
	page,
	context,
}, testInfo) => {
	await seed(page, testInfo.workerIndex.toString());

	// (The harness's next turn asks before it acts, as Claude Code does; the person's answer is
	// what the turn then says.)
	await script([
		{
			when: "what is stuck?",
			steps: [
				{
					mcp: {
						tool: "orch_chat_ask_permission",
						arguments: { tool_name: "Bash", input: { command: "git rebase origin/main" } },
					},
				},
			],
		},
	]);

	await page.goto("/ui/");
	await page.getByRole("button", { name: "Chat" }).click();
	await started(page);

	// (`@` completes a key from the project: the task this test seeded.)
	const message = inside(page, "Message");
	await message.click();
	await message.pressSequentially("look at @chat-");
	const completions = inside(page, "Completions");
	await expect(completions).toBeVisible();
	await completions.getByRole("button").first().click();
	// (The keys are the project's, and another run's are in it too: this one completes, whichever.)
	await expect(message).toHaveValue(/@chat-\d+\.1 /);

	// (The composer sends with Enter.)
	await message.fill("what is stuck?");
	await message.press("Enter");
	await expect(inside(page, "Transcript")).toContainText("what is stuck?");

	// (The harness asks, and the person allows it for the rest of the chat.)
	const asked = inside(page, "Waiting on you");
	await expect(asked).toBeVisible();
	await expect(asked).toContainText("git rebase origin/main");
	await asked.getByRole("button", { name: "Allow for this chat" }).click();
	await expect(asked).toBeHidden();
	await expect(inside(page, "Transcript")).toContainText('"behavior": "allow"');

	// (The same chat after a reload: the browser remembers which one it was, and the panel
	// remembers that it was open.)
	await page.reload();
	await expect(inside(page, "Transcript")).toContainText("what is stuck?");

	// (And the same chat in a window of its own — where the page's copy is closed, so the two
	// cannot disagree about what is being said.)
	const popped = context.waitForEvent("page");
	await inside(page, "Pop the chat out").click();
	const other = await popped;
	// (A window of its own loads the shell and the plugin's bundle again: it is the slowest page
	// in this test, so it is given longer than the rest.)
	await other.waitForLoadState();
	await expect(inside(other, "Transcript")).toContainText("what is stuck?", { timeout: 30_000 });
	await expect(inside(other, "Message")).toBeVisible({ timeout: 30_000 });
	await expect(panel(page)).toBeHidden();
});
