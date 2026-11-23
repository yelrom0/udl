import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KeyLink } from "./keyLink";

describe("<KeyLink>", () => {
	it("links a task, a feature or a question to its own page", () => {
		render(
			<>
				<KeyLink kind="task" label="29.2" />
				<KeyLink kind="feature" label="29" />
				<KeyLink kind="question" label="Q-8" />
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

	it("is mono, as a key is", () => {
		render(<KeyLink kind="task" label="29.2" />);

		expect(screen.getByRole("link", { name: "29.2" }).className).toContain("font-mono");
	});

	it("keeps a key with a slash in it whole", () => {
		render(<KeyLink kind="task" label="a/b" />);

		expect(screen.getByRole("link", { name: "a/b" }).getAttribute("href")).toBe(
			"/ui/p/orch/tasks/a%2Fb",
		);
	});
});
