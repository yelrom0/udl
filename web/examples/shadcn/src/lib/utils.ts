import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn's `cn`: class names joined, later ones winning where they clash. */
export function cn(...inputs: ClassValue[]): string {
	return twMerge(clsx(inputs));
}
