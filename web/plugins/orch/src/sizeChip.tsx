import { POINTS, type Size } from "./model";

/** What `<SizeChip>` is given: a task's size, or none (an unsized task shows nothing). */
export interface SizeChipProps {
	readonly size: Size | null;
}

/** What a size is, in words: its points. */
function wordsOf(size: Size): string {
	const points = POINTS[size];
	return points === 1 ? "1 point" : `${points} points`;
}

/**
 * A task's size (26.8), as one chip: the letter, with its points in the tooltip. An unsized task
 * has no chip at all — nothing is not a size.
 */
export function SizeChip({ size }: SizeChipProps) {
	if (size === null) {
		return null;
	}
	return (
		<span
			className="badge badge-ghost badge-sm font-mono border-base-content/20"
			title={wordsOf(size)}
		>
			{size}
		</span>
	);
}
