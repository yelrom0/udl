/**
 * The pages of a list: which one is shown, and the two buttons that move between them.
 */

export interface PagedProps {
	/** The page shown, from 1. */
	readonly page: number;
	/** How many there are (1 when the list is empty, so there is always a page). */
	readonly pages: number;
	readonly onChange: (page: number) => void;
}

export function Paged({ page, pages, onChange }: PagedProps) {
	const atStart = page <= 1;
	const atEnd = page >= pages;
	return (
		<nav className="join my-4" aria-label="Pages">
			<button
				type="button"
				className="btn join-item"
				disabled={atStart}
				onClick={() => onChange(page - 1)}
			>
				Previous
			</button>
			<span className="btn join-item pointer-events-none">
				Page {page} of {pages}
			</span>
			<button
				type="button"
				className="btn join-item"
				disabled={atEnd}
				onClick={() => onChange(page + 1)}
			>
				Next
			</button>
		</nav>
	);
}
