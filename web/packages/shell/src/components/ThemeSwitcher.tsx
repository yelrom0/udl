/**
 * The theme switcher: system, light or dark, in the navbar. The choice is the theme store's,
 * so it persists and follows the system while "system" is chosen.
 */

import { setTheme, THEMES, useTheme } from "../stores/theme";

const LABELS = { system: "System", light: "Light", dark: "Dark" } as const;

export function ThemeSwitcher() {
	const current = useTheme();
	return (
		<select
			className="select select-sm w-24"
			aria-label="Theme"
			value={current}
			onChange={(event) => {
				const chosen = event.target.value;
				for (const theme of THEMES) {
					if (theme === chosen) {
						setTheme(theme);
					}
				}
			}}
		>
			{THEMES.map((theme) => (
				<option key={theme} value={theme}>
					{LABELS[theme]}
				</option>
			))}
		</select>
	);
}
