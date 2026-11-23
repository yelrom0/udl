import type { ReactNode } from "react";

/** What a page is given by the shell's own frame: its heading and what the page says. */
export interface PageProps {
	readonly title: string;
	readonly children?: ReactNode;
}

/**
 * A page's frame (29.2): its heading, one line of what it is, and what it draws. Every route
 * the manifest contributes is drawn inside one, so a route that renders is a route that says
 * where the reader is.
 */
export function Page({ title, children }: PageProps) {
	return (
		<section className="flex flex-col gap-4">
			<header className="flex flex-wrap items-baseline justify-between gap-2">
				<h1 className="text-2xl font-semibold">{title}</h1>
			</header>
			{children}
		</section>
	);
}

/** What a page says before it has anything else: the one line of what it is for. */
export function Said({ children }: { readonly children: ReactNode }) {
	return <p className="text-sm opacity-70">{children}</p>;
}
