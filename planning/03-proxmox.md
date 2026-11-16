# UDL Phase 22: Proxmox plugin (`proxmox`)

> Part of udl's plan. [planning.md](../planning.md) holds the tracker line, the [Working agreement](../planning.md#working-agreement-tdd--typing) (TDD, `pyright --strict`, `make check`) and the Definition of Done, and they all apply here. Written by Claude on 5 Nov 2026.
>
> **What it is:** an integration that manages Proxmox VE virtual machines from udl, through its CLI, its HTTP API and, for read-only operations only, its MCP server. Its first users are Liively's `liively-dev` and `liively-stage` VMs. Liively's Phase 10, decisions T-23 and T-24 in `~/code/liively/planning/README.md`, moves dev and staging off AWS onto them. The VMs' configuration lives in Ansible playbooks in `liively-application-infra/onprem/`. This plugin **calls** those playbooks and never owns them.
>
> **Depends on:** Phase 11 (integrations and accounts) only, which is done. It is independent of Phases 13–21.

---

## Decisions (user, 5 Nov 2026)

| ID | Decision | Rejected alternatives |
|---|---|---|
| **P-D1** | **udl gets a `proxmox` integration.** The VMs' own configuration is Ansible, kept in `liively-application-infra/onprem/`; the plugin runs only the playbooks its settings list | Ansible playbooks inside udl; a separate tool outside udl |
| **P-D2** | **MCP gets the read-only operations only:** list VMs, status, snapshots, backup results. Start, shutdown, reboot, stop, snapshot, rollback, clone and running playbooks are CLI and HTTP API only, and never available to udl's AI actions either | Everything on MCP with a confirmation field; everything on MCP |
| **P-D3** | **Proxmox API token auth** (`ConnectMethod.TOKENS`): a token such as `udl@pve!udl`, its secret in udl's secret store, with a role limited to one pool (`PVEVMAdmin` on `/pool/liively` and `PVEAuditor` on `/`). The token is created by the user in Proxmox; udl never sees the root password | A username/password ticket login; a root token |
| **P-D4** | **The Proxmox API is called with `udl.http` directly** (`https://<host>:8006/api2/json`), with one Pydantic model per response, so the code stays `pyright --strict` | `proxmoxer` (untyped, would need stubs), or Ansible for every call |
| **P-D5** | **TLS:** Proxmox ships a self-signed certificate, so an account pins the host certificate's SHA-256 fingerprint (`fingerprint` option). `verify_tls: false` is not offered | Turning TLS verification off |
| **P-D6** | **Dangerous operations name what they act on:** `stop` and `rollback` need `confirm` equal to the VM's name, and refuse otherwise | A bare `--yes` flag |

---

## Progress tracker

- [ ] **22.0** **(Claude) Workspace:**
  - a udl checkout on thevault for the implementing agent (`git clone personalgit:yelrom0/udl ~/code/udl`);
  - `uv` installed from PyPI (`python3.14 -m pip install --user uv`), then `make check` green on `main`;
  - an `agent-push` profile for udl in oc-guard (gate: `make check`; written test-first in oc-guard);
  - `ansible-core` plus `community.proxmox` installed for the Liively playbooks (T-24).
- [ ] **22.1** 🔴→🟢 **Core: `OperationHints.mcp`** (default `True`).
  - The MCP server (`interfaces/mcp/server.py`) leaves out operations with `mcp=False`.
  - The AI action runtime refuses them as `NotAllowed`.
  - The operation itself also refuses when `current_caller` is MCP or an AI action (defence in depth).
  - Update the parity test: an `mcp=False` operation has a CLI command and an API route but no MCP tool.
- [ ] **22.2** 🔴→🟢 **`ProxmoxClient`** (`src/udl/plugins/integrations/proxmox/client.py`): typed calls and response models (list below), token auth header, fingerprint pinning, task polling (`wait_task(upid)`). Tested with `respx` and recorded JSON fixtures.
- [ ] **22.3** 🔴→🟢 **`ProxmoxIntegration`**: name `proxmox`, connect `TOKENS`, account options `{host, port=8006, fingerprint, pool="liively"}`. `identify()` calls `GET /version` and `GET /access/permissions`, and refuses a token that can't see the pool. `check()` reports the pool's VM count. It passes `tests/contract/test_integration_contract.py`.
- [ ] **22.4** 🔴→🟢 **Read-only operations** (MCP allowed; hints `read_only`, `idempotent`, `open_world`): `list_vms` (fans out), `vm_status`, `list_snapshots`, `backup_status`.
- [ ] **22.5** 🔴→🟢 **Power operations** (`mcp=False`): `start`, `shutdown`, `reboot`, and `stop` (destructive, with `confirm` per P-D6). Each waits for its Proxmox task and returns its exit status.
- [ ] **22.6** 🔴→🟢 **Snapshots** (`mcp=False`): `snapshot` (create, with a name and description) and `rollback` (destructive, with `confirm`).
- [ ] **22.7** 🔴→🟢 **`clone`** (`mcp=False`): from a template VM id into the pool, as a full clone, with the new VM's name. It returns the new VM id once the task finishes.
- [ ] **22.8** 🔴→🟢 **`run_playbook`** (`mcp=False`): runs one playbook named in the settings' allow-list, with `--limit <vm>`. It returns the exit code, the parsed `PLAY RECAP` and a log record id.
- [ ] **22.9** 🔴→🟢 **Watcher:** polls every 60 s, with the last-seen state and task id as its cursor. It publishes `proxmox.vm.status_changed`, `proxmox.backup.failed` and `proxmox.task.failed`, each once, so `notify` subscriptions can alert on them.
- [ ] **22.10** **Docs:** a README section (connecting a Proxmox account, the operations table with which ones are MCP-visible, the settings), a config example, and a note in `docs/plugins.md` on `OperationHints.mcp`.
- [ ] **22.11** **(Claude + user) Live check:**
  1. The user creates the pool and the API token in Proxmox.
  2. Claude connects an account and runs the read-only operations against the real host.
  3. The user runs `snapshot` and `rollback` on a throwaway VM.

---

## Spec

### Proxmox API calls (22.2)

| Method | Path (under `/api2/json`) | Model |
|---|---|---|
| GET | `/version` | `PveVersion {version, release, repoid}` |
| GET | `/access/permissions?path=/pool/{pool}` | `dict[str, dict[str, int]]` |
| GET | `/cluster/resources?type=vm` | `list[PveResource {vmid, name, node, status, pool?, template, maxmem, maxcpu, uptime?}]`, filtered to the account's pool |
| GET | `/nodes/{node}/qemu/{vmid}/status/current` | `PveVmStatus {status, qmpstatus?, uptime?, cpu, mem, maxmem, name}` |
| POST | `/nodes/{node}/qemu/{vmid}/status/{start\|shutdown\|reboot\|stop}` | UPID string |
| GET / POST | `/nodes/{node}/qemu/{vmid}/snapshot` | `list[PveSnapshot {name, description?, snaptime?, parent?}]` / UPID |
| POST | `/nodes/{node}/qemu/{vmid}/snapshot/{snapname}/rollback` | UPID |
| POST | `/nodes/{node}/qemu/{template}/clone` (`newid`, `name`, `pool`, `full=1`, `target?`) | UPID |
| GET | `/cluster/nextid` | `int` |
| GET | `/nodes/{node}/tasks/{upid}/status` | `PveTask {status, exitstatus?, type, id, starttime, endtime?}` |
| GET | `/nodes/{node}/tasks?typefilter=vzdump&limit=50` | `list[PveTaskSummary]` (for `backup_status` and the watcher) |

- **Auth header:** `Authorization: PVEAPIToken=<tokenid>=<secret>`. The secret comes only from the account's credentials and is never logged.
- **Errors:**
  - 401 → `ReauthRequired`;
  - 403 → `NotAllowed`, with a hint naming the missing privilege;
  - 5xx → udl's retry policy, then `PluginError`;
  - a fingerprint mismatch → `NotAllowed` ("The Proxmox host's certificate changed").
- **`wait_task(upid, timeout)`:** poll every 2 s until `status == "stopped"`. Then `exitstatus == "OK"` succeeds, and anything else raises `PluginError` carrying Proxmox's exit status.

### Settings (22.8)

```toml
[integrations.proxmox]
ansible_checkout = "~/code/liively/liively-application-infra/onprem"   # config file only: it widens what udl runs
inventory = "inventories/liively.yml"                                  # relative to the checkout
playbooks = { vm = "playbooks/vm.yml", baseline = "playbooks/baseline.yml" }  # the allow-list
playbook_timeout = "30m"
```

- `ansible_checkout` and `playbooks` widen what udl reaches, so they're read from the config file only, never through `udl plugins configure` (udl's existing rule).
- `run_playbook(account, {playbook, limit, extra_vars?})`:
  - checks `playbook` is a key of the allow-list;
  - checks `limit` is a VM in the account's pool;
  - only lets `extra_vars` keys match `^[a-z_][a-z0-9_]*$`, and values be strings or numbers;
  - runs `ansible-playbook -i <inventory> <path> --limit <limit>` through `udl.process` with the timeout, and declares `Requirement.binary("ansible-playbook")`;
  - never passes a shell string.

### Tests (every task)

- **Unit:** each client call (path, query, auth header, model parsing, every error mapping) and each operation's validation (pool membership, `confirm`, the playbook allow-list, `extra_vars`).
- **Parity:** `mcp=False` operations have CLI and API entries but no MCP tool, and AI actions refuse them.
- **Contract:** `ProxmoxIntegration` passes the integration contract suite.
- **Watcher:** an event is published once across restarts (the state cursor), and a failed vzdump becomes `proxmox.backup.failed`.
- **Coverage:** udl's coverage gate is a floor; cover the branches and error paths.
