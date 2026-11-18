/**
 * Signing in: the API key, which udl answers with a session cookie. A udl with no key set
 * answers everything, so this page is only reached when udl asked for one.
 */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { SignInError, signIn } from "../stores/session";

export const Route = createFileRoute("/login")({
	component: LoginPage,
});

function LoginPage() {
	const navigate = useNavigate();
	const [key, setKey] = useState("");
	const [refused, setRefused] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	return (
		<div className="flex min-h-screen items-center justify-center p-4">
			<form
				className="card w-full max-w-sm bg-base-200 p-6 shadow"
				onSubmit={(event) => {
					event.preventDefault();
					setBusy(true);
					setRefused(null);
					void signIn(key)
						.then(() => navigate({ to: "/" }))
						.catch((error: unknown) => {
							setRefused(error instanceof SignInError ? error.message : String(error));
						})
						.finally(() => setBusy(false));
				}}
			>
				<h1 className="card-title text-2xl">udl</h1>
				<p className="opacity-70">This server needs its API key.</p>
				<label className="label mt-4" htmlFor="api-key">
					API key
				</label>
				<input
					id="api-key"
					className="input w-full"
					type="password"
					autoComplete="current-password"
					value={key}
					onChange={(event) => setKey(event.target.value)}
				/>
				{refused === null ? null : (
					<p role="alert" className="mt-2 text-sm text-error">
						{refused}
					</p>
				)}
				<button type="submit" className="btn btn-primary mt-4" disabled={busy || key === ""}>
					Sign in
				</button>
				<p className="mt-2 text-xs opacity-60">It is `api.api_key` in the server's config.</p>
			</form>
		</div>
	);
}
