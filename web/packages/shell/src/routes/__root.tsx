/**
 * The page's frame: a daisyUI drawer (the nav, on a phone too) around a navbar (the live
 * connection, the theme, signing out) and the route's own page. It also decides whether there
 * is a page to show: udl answers a request without its key only with a session, so a page that
 * is not signed in goes to the login page.
 */

import {
	createRootRoute,
	Link,
	Outlet,
	redirect,
	useNavigate,
	useRouter,
} from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { ConfirmHost } from "../components/ConfirmHost";
import { ThemeSwitcher } from "../components/ThemeSwitcher";
import { startLive } from "../live/connection";
import { useLive } from "../stores/live";
import { useNav } from "../stores/plugins";
import { check, session, signOut, useSession } from "../stores/session";
import { applyTheme, useTheme } from "../stores/theme";

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
	const router = useRouter();
	const state = useSession();
	const theme = useTheme();
	const connection = useLive((live) => live.connection);
	const nav = useRef<HTMLInputElement>(null);
	// (The shell's own pages and each plugin's: one list, from 18.9's registry.)
	const pages = useNav();

	useEffect(() => {
		applyTheme(theme);
	}, [theme]);

	useEffect(() => {
		if (state === "signed-out") {
			void navigate({ to: "/login" });
		}
	}, [state, navigate]);

	// (Live data only while the page may read it: udl's streams are the API's, behind the session.)
	useEffect(() => {
		if (state !== "signed-in") {
			return;
		}
		const live = startLive({ reload: () => void router.invalidate() });
		return live.stop;
	}, [state, router]);

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
				<ConfirmHost />
			</div>
			<div className="drawer-side z-10">
				<label htmlFor="nav" className="drawer-overlay" aria-label="Close the menu" />
				<ul className="menu min-h-full w-56 bg-base-200 p-2">
					{pages.map((page) => (
						<li key={`${page.plugin.name}:${page.to}`}>
							<Link
								to={page.to}
								activeProps={{ className: "menu-active" }}
								onClick={() => {
									// (On a phone the nav covers the page: choosing a page closes it.)
									if (nav.current !== null) {
										nav.current.checked = false;
									}
								}}
							>
								{page.title}
							</Link>
						</li>
					))}
				</ul>
			</div>
		</div>
	);
}
