import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SizeChip } from "./sizeChip";

describe("<SizeChip>", () => {
	it("shows a size's letter, with its points in the tooltip", () => {
		render(
			<>
				<SizeChip size="S" />
				<SizeChip size="M" />
				<SizeChip size="L" />
				<SizeChip size="XL" />
			</>,
		);

		expect(screen.getByTitle("1 point").textContent).toBe("S");
		expect(screen.getByTitle("3 points").textContent).toBe("M");
		expect(screen.getByTitle("5 points").textContent).toBe("L");
		expect(screen.getByTitle("8 points").textContent).toBe("XL");
	});

	it("is mono, and nothing at all when the task has no size", () => {
		const { container } = render(<SizeChip size={null} />);

		expect(container.innerHTML).toBe("");
	});

	it("is mono, as a key is", () => {
		render(<SizeChip size="M" />);

		expect(screen.getByTitle("3 points").className).toContain("font-mono");
	});
});
