/**
 * --------------
 * - Accounts.tsx -
 * --------------
 *
 * An integration's accounts (24.6): the ones udl has, each checked or disconnected from here,
 * and the form that connects another — how it connects (as the integration says it can), its
 * name, what for, and its tokens when they are what connects it. OAuth says where to go next.
 *
 * Since 24.7 the integration's own UI plugin may draw the setup instead (`accountSetup`): the
 * accounts it has are still udl's to list, check and disconnect.
 */

import type { UdlApi } from "@udl/api";
import { fieldsOf, type JsonObject } from "@udl/ui-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Registry } from "../plugins/registry";
import { contributed, shellRegistry } from "../plugins/shell";
import { PluginSlot } from "../plugins/slot";
import { ask } from "../stores/confirms";
import { jsonObject, saidBy } from "./run";
import { SchemaForm } from "./SchemaForm";
import type { GeneratedAccounts, GeneratedPlugin } from "./spec";

/** What udl says an account is (`AccountInfo`, as the page reads it). */
interface Account {
	readonly id: string;
	readonly account: string;
	readonly integration: string;
	readonly enabled: boolean;
	readonly access: readonly string[];
	readonly connected_at: string | null;
}

/** What udl says checking one ended in (`AccountHealth`, as the page reads it). */
interface Health {
	readonly ok: boolean;
	readonly problems: readonly string[];
}

export interface AccountsTabProps {
	readonly plugin: GeneratedPlugin;
	readonly api: UdlApi;
	/** Where the contributions are (a test's own; the shell's when none is given). */
	readonly registry?: Registry;
}

/** The accounts of `plugin`, and connecting another. */
export function AccountsTab({ plugin, api, registry = shellRegistry }: AccountsTabProps) {
	const [accounts, setAccounts] = useState<readonly Account[] | null>(null);
	const [said, setSaid] = useState<{ message: string; hint: string | null } | null>(null);
	const [checked, setChecked] = useState<Record<string, Health>>({});
	const live = useRef(true);

	useEffect(() => {
		live.current = true;
		return () => {
			live.current = false;
		};
	}, []);

	const read = useCallback(async () => {
		try {
			const answered = await api.call("list_accounts");
			if (live.current) {
				setAccounts(
					answered.accounts
						.filter((one) => one.integration === plugin.name)
						.map((one) => ({
							id: one.id,
							account: one.account,
							integration: one.integration,
							enabled: one.enabled,
							access: one.access ?? [],
							connected_at: one.connected_at ?? null,
						})),
				);
			}
		} catch (error) {
			if (live.current) {
				setSaid(saidBy(error));
			}
		}
	}, [api, plugin.name]);

	useEffect(() => {
		void read();
	}, [read]);

	const check = async (account: Account) => {
		try {
			// (udl takes the account's ref — `integration:name` — not its own id.)
			const answered = await api.call("check_account", undefined, {
				account: account.account,
			});
			const health: Health = { ok: answered.ok, problems: answered.problems ?? [] };
			if (live.current) {
				setChecked((known) => ({ ...known, [account.id]: health }));
			}
		} catch (error) {
			if (live.current) {
				setSaid(saidBy(error));
			}
		}
	};

	const disconnect = async (account: Account) => {
		const yes = await ask(`Disconnect ${account.account}? It can be connected again.`, {
			ok: "Disconnect",
		});
		if (!yes) {
			return;
		}
		try {
			await api.call("disconnect_account", undefined, { account: account.account });
			if (live.current) {
				setChecked((known) => {
					const { [account.id]: gone, ...rest } = known;
					return gone === undefined ? known : rest;
				});
				await read();
			}
		} catch (error) {
			if (live.current) {
				setSaid(saidBy(error));
			}
		}
	};

	const accounts_ = plugin.accounts;
	if (accounts_ === null) {
		return null;
	}

	return (
		<div className="flex flex-col gap-6">
			{said !== null && (
				<div role="alert" className="alert alert-error">
					<span>
						{said.message}
						{said.hint !== null && <span className="block text-xs opacity-80">{said.hint}</span>}
					</span>
				</div>
			)}
			{accounts !== null && accounts.length === 0 && <p className="opacity-70">No accounts yet.</p>}
			{accounts !== null && accounts.length > 0 && (
				<div className="overflow-x-auto">
					<table className="table">
						<thead>
							<tr>
								<th>Account</th>
								<th>Access</th>
								<th>Status</th>
								<th />
							</tr>
						</thead>
						<tbody>
							{accounts.map((account) => (
								<tr key={account.id}>
									<td>{account.account}</td>
									<td>{account.access.join(", ") || "—"}</td>
									<td>
										{checked[account.id] === undefined ? (
											<span className="opacity-70">
												{account.connected_at === null ? "Not connected" : "Connected"}
											</span>
										) : checked[account.id]?.ok === true ? (
											<span className="badge badge-success badge-sm">ok</span>
										) : (
											<span className="badge badge-error badge-sm">
												{(checked[account.id]?.problems ?? []).join("; ") || "a problem"}
											</span>
										)}
									</td>
									<td className="flex gap-2">
										<button
											type="button"
											className="btn btn-xs"
											onClick={() => void check(account)}
										>
											Check
										</button>
										<button
											type="button"
											className="btn btn-xs btn-error"
											onClick={() => void disconnect(account)}
										>
											Disconnect
										</button>
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
			{contributed("accountSetup", plugin.name, registry) ? (
				<PluginSlot
					point="accountSetup"
					lookup={plugin.name}
					registry={registry}
					replace
					props={{ integration: plugin.name, done: () => void read() }}
				/>
			) : (
				<ConnectForm
					plugin={plugin}
					accounts={accounts_}
					api={api}
					onConnected={() => void read()}
				/>
			)}
		</div>
	);
}

/** The form that connects an account: its name, what for, its tokens, and how (when the
 * integration offers more than one way). */
function ConnectForm({
	plugin,
	accounts,
	api,
	onConnected,
}: {
	readonly plugin: GeneratedPlugin;
	readonly accounts: GeneratedAccounts;
	readonly api: UdlApi;
	readonly onConnected: () => void;
}) {
	const [name, setName] = useState("");
	// (What it is connected for starts at the integration's default: the first level it lists.)
	const [access, setAccess] = useState<string[]>(accounts.access_levels.slice(0, 1));
	const [method, setMethod] = useState<string>(accounts.connect[0] ?? "oauth");
	const [tokens, setTokens] = useState<JsonObject>({});
	const [options, setOptions] = useState<JsonObject>({});
	const [said, setSaid] = useState<{ message: string; hint: string | null } | null>(null);
	const [busy, setBusy] = useState(false);
	const [waiting, setWaiting] = useState<string | null>(null);

	const tokensWanted = method === "tokens";
	const optionsSchema = jsonObject(accounts.options_schema);
	const optionsFields = fieldsOf(optionsSchema);

	const connect = async () => {
		setBusy(true);
		setSaid(null);
		setWaiting(null);
		try {
			const answered = await api.call("connect_account", {
				integration: plugin.name,
				name,
				reconnect: false,
				...(access.length > 0 ? { access } : {}),
				...(accounts.connect.length > 1 && isConnectMethod(method) ? { method } : {}),
				...(tokensWanted && Object.keys(tokens).length > 0 ? { tokens: strings(tokens) } : {}),
				...(Object.keys(options).length > 0 ? { options } : {}),
			});
			// (OAuth: udl is waiting for the browser to come back, and the page says where to go.
			// Left out is no OAuth at all, not one without a page.)
			const page = answered.authorization_url ?? null;
			if (page !== null) {
				setWaiting(page);
			} else {
				setName("");
				setTokens({});
				setOptions({});
				// (What it is connected for starts where it started: the first level udl lists.)
				setAccess(accounts.access_levels.slice(0, 1));
				onConnected();
			}
		} catch (error) {
			setSaid(saidBy(error));
		} finally {
			setBusy(false);
		}
	};

	return (
		<form
			className="card bg-base-200"
			onSubmit={(event) => {
				event.preventDefault();
				void connect();
			}}
		>
			<div className="card-body gap-3">
				<h3 className="card-title">Connect an account</h3>
				<label className="form-control">
					<span className="label-text">Name</span>
					<input
						className="input input-bordered"
						value={name}
						required
						pattern="[a-z0-9][a-z0-9._-]*"
						onChange={(event) => setName(event.target.value)}
					/>
				</label>
				{accounts.access_levels.length > 1 && (
					<fieldset className="form-control">
						<legend className="label-text">What for</legend>
						{accounts.access_levels.map((level) => (
							<label key={level} className="label cursor-pointer justify-start gap-2">
								<input
									type="checkbox"
									className="checkbox checkbox-sm"
									checked={access.includes(level)}
									onChange={(event) =>
										setAccess((chosen) =>
											event.target.checked
												? [...chosen, level]
												: chosen.filter((one) => one !== level),
										)
									}
								/>
								<span className="label-text">{level}</span>
							</label>
						))}
					</fieldset>
				)}
				{accounts.connect.length > 1 && (
					<label className="form-control">
						<span className="label-text">How</span>
						<select
							className="select select-bordered"
							value={method}
							onChange={(event) => setMethod(event.target.value)}
						>
							{accounts.connect.map((way) => (
								<option key={way} value={way}>
									{way}
								</option>
							))}
						</select>
					</label>
				)}
				{tokensWanted && <TokensEditor tokens={tokens} onChange={setTokens} />}
				{optionsFields.length > 0 && (
					<SchemaForm schema={optionsSchema} value={options} onChange={setOptions} />
				)}
				<div className="card-actions items-center">
					<button type="submit" className="btn btn-primary btn-sm" disabled={busy || name === ""}>
						{method === "oauth" ? "Sign in" : "Connect"}
					</button>
				</div>
				{waiting !== null && (
					<output className="alert">
						<span>
							Finish connecting it in your browser:{" "}
							<a className="link" href={waiting} target="_blank" rel="noreferrer">
								{waiting}
							</a>
						</span>
					</output>
				)}
				{said !== null && (
					<div role="alert" className="alert alert-error">
						<span>
							{said.message}
							{said.hint !== null && <span className="block text-xs opacity-80">{said.hint}</span>}
						</span>
					</div>
				)}
			</div>
		</form>
	);
}

/** `values`'s text values only (a token's value is a string, and a secret is never drawn back). */
function strings(values: JsonObject): Record<string, string> {
	const said: Record<string, string> = {};
	for (const [name, value] of Object.entries(values)) {
		if (typeof value === "string") {
			said[name] = value;
		}
	}
	return said;
}

/** How an account connects, as the API names the ways (`ConnectMethod`). */
const CONNECT_METHODS = ["oauth", "tokens", "none"] as const;

function isConnectMethod(value: string): value is (typeof CONNECT_METHODS)[number] {
	return (CONNECT_METHODS as readonly string[]).includes(value);
}

/**
 * An integration's tokens, by name (`ConnectAccountRequest.tokens`): udl says a token by its
 * name only in the integration's docs, so the page cannot draw the fields — it takes a name and
 * a secret for each, and every value is a password box.
 */
function TokensEditor({
	tokens,
	onChange,
}: {
	readonly tokens: JsonObject;
	readonly onChange: (tokens: JsonObject) => void;
}) {
	const names = Object.keys(tokens);
	return (
		<fieldset className="form-control gap-2">
			<legend className="label-text">Tokens</legend>
			{names.map((name) => (
				<div key={name} className="flex items-center gap-2">
					<span className="w-40 font-mono text-sm">{name}</span>
					<input
						type="password"
						aria-label={name}
						className="input input-bordered input-sm flex-1"
						value={typeof tokens[name] === "string" ? String(tokens[name]) : ""}
						onChange={(event) => onChange({ ...tokens, [name]: event.target.value })}
					/>
					<button
						type="button"
						className="btn btn-ghost btn-xs"
						aria-label={`Forget ${name}`}
						onClick={() => {
							const { [name]: gone, ...rest } = tokens;
							onChange(gone === undefined ? tokens : rest);
						}}
					>
						✕
					</button>
				</div>
			))}
			<AddToken
				onAdd={(name) => {
					if (name !== "" && !names.includes(name)) {
						onChange({ ...tokens, [name]: "" });
					}
				}}
			/>
		</fieldset>
	);
}

/** The box a token's name is typed in, and the button that adds it. */
function AddToken({ onAdd }: { readonly onAdd: (name: string) => void }) {
	const [name, setName] = useState("");
	return (
		<div className="flex items-center gap-2">
			<input
				className="input input-bordered input-sm w-40"
				placeholder="token name"
				aria-label="Token name"
				value={name}
				onChange={(event) => setName(event.target.value)}
			/>
			<button
				type="button"
				className="btn btn-sm"
				onClick={() => {
					onAdd(name.trim());
					setName("");
				}}
			>
				Add token
			</button>
		</div>
	);
}
