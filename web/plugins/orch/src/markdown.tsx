import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo } from "react";

/** What `<Markdown>` is given: the text, as a spec, a note or a question's thread writes it. */
export interface MarkdownProps {
	readonly text: string;
}

/**
 * A link, made safe to open: `target="_blank"` with `rel="noopener noreferrer"`, so a page an
 * agent linked to cannot reach back through `window.opener` — and so the reader keeps udl's
 * page open behind it.
 */
function opened(html: string): string {
	const page = new DOMParser().parseFromString(html, "text/html");
	for (const link of page.querySelectorAll("a[href]")) {
		link.setAttribute("target", "_blank");
		link.setAttribute("rel", "noopener noreferrer");
	}
	return page.body.innerHTML;
}

/** `text` as HTML: parsed as markdown, sanitised, its links opened in a new tab. */
export function markdownOf(text: string): string {
	// (No inline styles: the shell's CSP forbids them, and a spec has no reason to carry one.)
	return opened(
		DOMPurify.sanitize(marked.parse(text, { async: false }), { FORBID_ATTR: ["style"] }),
	);
}

/**
 * Markdown, drawn (29.2): an agent's spec, note or question is text it wrote, so it is
 * sanitised before it is drawn — no script, no handler, no inline style — and its links open in
 * a new tab.
 */
export function Markdown({ text }: MarkdownProps) {
	const html = useMemo(() => markdownOf(text), [text]);
	return (
		<div
			className="flex flex-col gap-2 text-sm [&_a]:link [&_code]:font-mono [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:text-lg [&_h2]:font-semibold [&_ul]:list-disc [&_ul]:pl-5"
			// biome-ignore lint/security/noDangerouslySetInnerHtml: (sanitised by DOMPurify, above.)
			dangerouslySetInnerHTML={{ __html: html }}
		/>
	);
}
