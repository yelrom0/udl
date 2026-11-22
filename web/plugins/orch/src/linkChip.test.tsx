import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LinkChip } from "./linkChip";

describe("<LinkChip>", () => {
	it("draws an open pull as a real link to it, with its number, its checks and the bot's word", () => {
		render(
			<LinkChip
				kind="pull"
				url="https://github.com/yelrom0/udl/pull/78"
				state={{ checks: "pass", review: "approved" }}
			/>,
		);

		const chip = screen.getByRole("link", { name: /#78/ });
		expect(chip.getAttribute("href")).toBe("https://github.com/yelrom0/udl/pull/78");
		expect(chip.getAttribute("target")).toBe("_blank");
		expect(chip.getAttribute("rel")).toBe("noopener noreferrer");
		expect(chip.querySelector(".checks-pass")).not.toBeNull();
		expect(chip.textContent).toContain("✓");
	});

	it("says when the checks are running or have failed, and when changes were asked", () => {
		render(
			<LinkChip
				kind="pull"
				url="https://github.com/yelrom0/udl/pull/41"
				state={{ checks: "pending", review: "changes" }}
			/>,
		);

		const chip = screen.getByRole("link", { name: /#41/ });
		expect(chip.querySelector(".checks-pending")).not.toBeNull();
		expect(chip.textContent).toContain("✎");
	});

	it("shows a commit's short SHA, a branch's name and a file's path in mono", () => {
		render(
			<>
				<LinkChip kind="commit" url="https://github.com/yelrom0/udl/commit/049b0eaf00" />
				<LinkChip kind="branch" url="https://github.com/yelrom0/udl/tree/feat/29.2" />
				<LinkChip kind="file" url="https://github.com/yelrom0/udl/blob/main/src/x.py" />
			</>,
		);

		expect(screen.getByText("049b0ea").className).toContain("font-mono");
		expect(screen.getByText("feat/29.2").className).toContain("font-mono");
		expect(screen.getByText("src/x.py").className).toContain("font-mono");
	});

	it("points a task, a feature and a question at the plugin's own pages", () => {
		render(
			<>
				<LinkChip kind="task" url="udl:task/udl/29.2" />
				<LinkChip kind="feature" url="udl:feature/udl/29" />
				<LinkChip kind="question" url="udl:question/udl/Q-8" />
			</>,
		);

		expect(screen.getByRole("link", { name: "29.2" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/29.2",
		);
		expect(screen.getByRole("link", { name: "29" }).getAttribute("href")).toBe(
			"/ui/p/orch/features/29",
		);
		expect(screen.getByRole("link", { name: "Q-8" }).getAttribute("href")).toBe(
			"/ui/p/orch/questions/Q-8",
		);
	});

	it("draws a stitch screen by its title, and a URL that is not one as it is", () => {
		render(
			<>
				<LinkChip kind="stitch" url="https://stitch.withgoogle.com/x" title="Inbox" />
				<LinkChip kind="url" url="not a url at all" />
				<LinkChip kind="doc" url="https://example.org/c" title="" />
			</>,
		);

		expect(screen.getByRole("link", { name: /Inbox/ })).toBeTruthy();
		expect(screen.getByText("not a url at all")).toBeTruthy();
		expect(screen.getByText("example.org")).toBeTruthy();
	});

	it("falls back to the last part of a URL that is not shaped as its kind expects", () => {
		render(
			<>
				<LinkChip kind="pull" url="https://github.com/yelrom0/udl/pulls?q=open" />
				<LinkChip kind="branch" url="https://github.com/yelrom0/udl/branches" />
				<LinkChip kind="file" url="https://github.com/yelrom0/udl/files" />
				<LinkChip kind="task" url="udl:action/udl/A-1" />
				<LinkChip kind="ci_run" url="https://github.com/yelrom0/udl/actions/runs/12345" />
			</>,
		);

		expect(screen.getByRole("link", { name: /#pulls\?q=open/ })).toBeTruthy();
		expect(screen.getByText("branches")).toBeTruthy();
		expect(screen.getByText("files")).toBeTruthy();
		expect(screen.getByText("run 12345")).toBeTruthy();
		expect(screen.getByRole("link", { name: "A-1" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/A-1",
		);
	});

	it("colours a pull's checks for what they are, and none when it has no state", () => {
		render(
			<>
				<LinkChip
					kind="pull"
					url="https://github.com/yelrom0/udl/pull/1"
					state={{ checks: "fail" }}
				/>
				<LinkChip
					kind="pull"
					url="https://github.com/yelrom0/udl/pull/2"
					state={{ checks: "pending" }}
				/>
				<LinkChip kind="pull" url="https://github.com/yelrom0/udl/pull/3" state={{}} />
			</>,
		);

		expect(document.querySelector(".checks-fail")).not.toBeNull();
		expect(document.querySelector(".checks-pending")).not.toBeNull();
		expect(document.querySelector(".checks-none")).not.toBeNull();
	});

	it("draws a doc or a URL by its title, or its host when it has none", () => {
		render(
			<>
				<LinkChip kind="doc" url="https://example.com/a" title="The plan" />
				<LinkChip kind="url" url="https://example.com/b" />
			</>,
		);

		expect(screen.getByRole("link", { name: /The plan/ })).toBeTruthy();
		expect(screen.getByRole("link", { name: /example\.com/ })).toBeTruthy();
	});
});
