/**
 * The page's frame: a daisyUI drawer (the nav, on a phone too) around a navbar (the live
 * connection, the theme, signing out) and the route's own page. It also decides whether there
 * is a page to show: udl answers a request without its key only with a session, so a page that
 * is not signed in goes to the login page.
 */

import { createRootRoute, Link, Outlet, redirect, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { ThemeSwitcher } from "../components/ThemeSwitcher";
import { useLive } from "../stores/live";
import { check, session, signOut, useSession } from "../stores/session";
import { applyTheme, useTheme } from "../stores/theme";

/** One item of the nav: where it goes, and what it says. */
export const NAV = [
	{ to: "/", label: "Dashboard" },
	{ to: "/downloads", label: "Downloads" },
	{ to: "/sites", label: "Sites" },
	{ to: "/chains", label: "Chains" },
	{ to: "/events", label: "Events" },
	{ to: "/subscriptions", label: "Subscriptions" },
	{ to: "/actions", label: "Actions" },
	{ to: "/runs", label: "Runs" },
	{ to: "/schedules", label: "Schedules" },
	{ to: "/ai", label: "AI" },
	{ to: "/accounts", label: "Accounts" },
	{ to: "/plugins", label: "Plugins" },
	{ to: "/databases", label: "Databases" },
	{ to: "/settings", label: "Settings" },
] as const;

export const Route = createRootRoute({
	beforeLoad: async ({ location }) => {
		// (Asked once a visit: the store keeps the answer, and signing in or out sets it.)
		const known = session.getState().state;
		const state = known === "unknown" ? await check() : known;
		if (state === "signed-out" && location.pathname !== "/login") {
			throw redirect({ to: "/login" });
		}
	},
	component: Layout,
});

function Layout() {
	const navigate = useNavigate();
	const state = useSession();
	const theme = useTheme();
	const connection = useLive((live) => live.connection);
	const nav = useRef<HTMLInputElement>(null);

	useEffect(() => {
		applyTheme(theme);
	}, [theme]);

	useEffect(() => {
		if (state === "signed-out") {
			void navigate({ to: "/login" });
		}
	}, [state, navigate]);

	// (The login page, or the page udl said to show before signing in: `beforeLoad` asked.)
	if (state !== "signed-in") {
		return <Outlet />;
	}

	return (
		<div className="drawer lg:drawer-open">
			<input ref={nav} id="nav" type="checkbox" className="drawer-toggle" />
			<div className="drawer-content flex min-h-screen flex-col">
				<header className="navbar bg-base-200">
					<div className="flex-none lg:hidden">
						{/* (daisyUI's drawer is a checkbox; the button is what a keyboard and a screen
						    reader can use.) */}
						<button
							type="button"
							className="btn btn-square btn-ghost"
							aria-label="Menu"
							onClick={() => {
								if (nav.current !== null) {
									nav.current.checked = !nav.current.checked;
								}
							}}
						>
							☰
						</button>
					</div>
					<div className="flex-1">
						<Link to="/" className="btn btn-ghost text-xl">
							udl
						</Link>
					</div>
					<div className="flex-none gap-2">
						<span
							className={`badge ${connection === "live" ? "badge-success" : "badge-ghost"}`}
							title="Live data"
						>
							{connection}
						</span>
						<ThemeSwitcher />
						<button
							type="button"
							className="btn btn-sm"
							onClick={() => {
								void signOut().then(() => navigate({ to: "/login" }));
							}}
						>
							Sign out
						</button>
					</div>
				</header>
				<main className="flex-1 p-4 lg:p-8">
					<Outlet />
				</main>
			</div>
			<div className="drawer-side z-10">
				<label htmlFor="nav" className="drawer-overlay" aria-label="Close the menu" />
				<ul className="menu min-h-full w-56 bg-base-200 p-2">
					{NAV.map((item) => (
						<li key={item.to}>
							<Link to={item.to} activeProps={{ className: "menu-active" }}>
								{item.label}
							</Link>
						</li>
					))}
				</ul>
			</div>
		</div>
	);
}
