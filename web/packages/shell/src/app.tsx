import { useEffect } from "react";
import { applyTheme, useTheme } from "./stores/theme";

/** The shell, at first: a page that says udl. The layout, routes and state come in 18.5. */
export function App() {
	const chosen = useTheme();
	useEffect(() => {
		applyTheme(chosen);
	}, [chosen]);

	return (
		<main className="m-8">
			<h1 className="text-3xl font-bold">udl</h1>
		</main>
	);
}
