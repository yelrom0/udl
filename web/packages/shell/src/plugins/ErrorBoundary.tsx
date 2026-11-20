/**
 * What a slot draws when the thing in it fails: an error card, in place of the contribution.
 * React's own boundary is a class (there is no hook for it), so this is one, and a plugin's
 * mount is caught where it is called too (an imperative mount throws outside React's tree).
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorCard } from "../components/ErrorCard";

export interface ErrorBoundaryProps {
	readonly children: ReactNode;
	readonly onError?: (error: unknown) => void;
}

export interface ErrorBoundaryState {
	readonly failed: unknown;
}

/** A part of the page whose failure is a card, not a blank screen. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
	override state: ErrorBoundaryState = { failed: null };

	static getDerivedStateFromError(failed: unknown): ErrorBoundaryState {
		return { failed };
	}

	override componentDidCatch(error: unknown, _info: ErrorInfo): void {
		this.props.onError?.(error);
	}

	override render(): ReactNode {
		if (this.state.failed !== null) {
			return <ErrorCard error={this.state.failed} />;
		}
		return this.props.children;
	}
}
