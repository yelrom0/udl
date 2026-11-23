import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Links } from "./links";

const LINKS = [
	{ link_kind: "pull", url: "https://github.com/o/r/pull/99", title: "99", state: {} },
	{
		link_kind: "commit",
		url: "https://github.com/o/r/commit/abc1234def",
		title: "",
		state: {},
	},
	{ link_kind: "task", url: "udl:task/udl/29.3", title: "", state: {} },
];

describe("<Links>", () => {
	it("draws one chip per link, by its kind", () => {
		render(<Links links={LINKS} />);

		const list = screen.getByRole("list", { name: "Links" });
		const links = screen.getAllByRole("link");
		expect(links).toHaveLength(3);
		// (A pull is its number, a commit its SHA's first seven, an internal kind its key.)
		expect(list.textContent).toContain("#99");
		expect(list.textContent).toContain("abc1234");
		expect(links[2]?.getAttribute("href")).toBe("/ui/p/orch/tasks/29.3");
	});

	it("says when there are none", () => {
		render(<Links links={[]} />);

		expect(screen.getByText("No links yet.")).toBeTruthy();
	});

	it("is read-only when it is given nothing to add with", () => {
		render(<Links links={LINKS} />);

		expect(screen.queryByRole("button", { name: "Add link" })).toBeNull();
	});

	it("adds one by kind and URL", async () => {
		const add = vi.fn(async () => {});
		render(<Links links={[]} onAdd={add} />);

		fireEvent.change(screen.getByRole("combobox", { name: "Kind" }), {
			target: { value: "pull" },
		});
		fireEvent.change(screen.getByRole("textbox", { name: "URL" }), {
			target: { value: "https://github.com/o/r/pull/99" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add link" }));

		expect(add).toHaveBeenCalledWith({
			link_kind: "pull",
			url: "https://github.com/o/r/pull/99",
		});
	});

	it("says what udl answered when it refuses a link", async () => {
		const add = vi.fn(async () => {
			throw new Error("a pull's URL is not a task's");
		});
		render(<Links links={[]} onAdd={add} />);

		fireEvent.change(screen.getByRole("textbox", { name: "URL" }), {
			target: { value: "nope" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add link" }));

		expect(await screen.findByText(/a pull's URL is not a task's/)).toBeTruthy();
	});
});
