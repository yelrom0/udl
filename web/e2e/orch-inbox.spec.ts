import { expect, type Page, test } from "@playwright/test";

/**
 * 29.3's proof, in a real browser: the inbox is the home route, a blocking question is pinned on
 * top with its options as one click, and answering one makes it leave.
 */

/** A write from outside the page: the session's cookie, the CSRF header and the page's origin. */
async function call(page: Page, name: string, body: object): Promise<unknown> {
	const answer = await page.request.post(`/plugins/orch/${name}`, {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: body,
	});
	expect(answer.ok(), `${name} answered ${answer.status()}: ${await answer.text()}`).toBe(true);
	return await answer.json();
}

/**
 * The project, a feature and a task, and one blocking question with two options. Light and dark
 * run against one udl, so a retry finds the question its first try left open: that is the one to
 * answer.
 */
async function seeded(page: Page): Promise<void> {
	await call(page, "set_project", { name: "udl", title: "udl" });
	await call(page, "set_feature", { key: "29", title: "The web UI" });
	await call(page, "set_task", { key: "29.3", feature: "29", title: "The inbox" });
	const asked = await page.request.post("/plugins/orch/ask", {
		headers: { "X-UDL-CSRF": "1", Origin: new URL(page.url()).origin },
		data: {
			task: "29.3",
			title: "Should a task moved between agents keep its worktree?",
			text: "A task moved from one agent to another keeps its branch and its worktree.",
			options: ["Keep it", "Make a new one"],
			blocking: true,
		},
	});
	// (409: this task already waits on the question a retry asked.)
	expect([200, 201, 409]).toContain(asked.status());
}

/** The key of the one open blocking question. */
async function blockingKey(page: Page): Promise<string> {
	const inbox = (await call(page, "inbox", {})) as {
		questions?: { key: string; blocking: boolean }[];
	};
	const found = (inbox.questions ?? []).find((one) => one.blocking);
	expect(found, "a blocking question is open").toBeTruthy();
	return found?.key ?? "";
}

test("the inbox answers a blocking question in one click, and it leaves (29.3)", async ({
	page,
}) => {
	// (The page is signed in by `auth.setup.ts`; it is loaded first so its own origin is known.)
	await page.goto("/ui/");
	await seeded(page);
	const key = await blockingKey(page);
	await page.goto("/ui/p/orch/inbox");

	// (The plugin draws in its own shadow root; Playwright's locators see into it.)
	const pinned = page.getByRole("region", { name: "Blocking" });
	await expect(pinned).toBeVisible();
	await expect(
		pinned.getByRole("link", { name: "Should a task moved between agents keep its worktree?" }),
	).toBeVisible();
	// (Its task is a link to the task's own page.)
	await expect(pinned.getByRole("link", { name: "29.3" })).toHaveAttribute(
		"href",
		"/ui/p/orch/tasks/29.3",
	);

	await page.getByRole("button", { name: "Keep it" }).click();

	// (It leaves: nothing is pinned, and nothing is being asked.)
	await expect(page.getByRole("region", { name: "Blocking" })).toHaveCount(0);
	await expect(page.getByText("Nothing is being asked.")).toBeVisible();

	// (And udl has the answer, not just the page: the question is answered, with what was clicked.)
	// (`orch_get_question` answers the question, its thread, and what it names.)
	const found = (await call(page, "get_question", { key })) as {
		question?: { state?: string; answer?: string | null };
	};
	expect(found.question?.state).toBe("answered");
	expect(found.question?.answer).toBe("Keep it");
});
