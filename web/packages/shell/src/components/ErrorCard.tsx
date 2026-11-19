/**
 * What went wrong, and what to do about it: udl's own message and hint when it sent them
 * (`UdlError`), or the error's message, and a button to try again.
 */

import { UdlError } from "@udl/api";

export interface ErrorCardProps {
	readonly error: unknown;
	readonly onRetry?: () => void;
}

/** What to show for `error`: udl's message and hint, or the error's own message. */
export function describe(error: unknown): { message: string; hint: string | null } {
	if (error instanceof UdlError) {
		return { message: error.info.message, hint: error.info.hint ?? null };
	}
	if (error instanceof Error) {
		return { message: error.message, hint: null };
	}
	return { message: String(error), hint: null };
}

export function ErrorCard({ error, onRetry }: ErrorCardProps) {
	const { message, hint } = describe(error);
	return (
		<div role="alert" className="alert alert-error my-4">
			<div>
				<p className="font-semibold">{message}</p>
				{hint === null ? null : <p className="text-sm">{hint}</p>}
				{onRetry === undefined ? null : (
					<button type="button" className="btn btn-sm mt-2" onClick={onRetry}>
						Try again
					</button>
				)}
			</div>
		</div>
	);
}
