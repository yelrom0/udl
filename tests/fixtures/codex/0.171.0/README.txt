Recorded by Claude, 21 Nov 2026, with codex-cli 0.171.0 on jeff-wiggle (not signed in).
events.jsonl: codex exec --json --profile local --sandbox read-only --cd <repo> -   (prompt on stdin)
events-resume.jsonl: codex exec resume --json -c model_provider="jeff-wiggle" -c model="qwen3.6-35b-a3b" <thread id> -
The local profile: ~/.codex/config.toml (provider jeff-wiggle, key from LLAMA_API_KEY) and ~/.codex/local.config.toml.

turn-with-tools.jsonl: NOT a recording. It is written from the CLI's own field shapes (codex-cli
0.171.0): a turn that runs a command (`command_execution`, whose `command` is a string) and calls
an MCP tool (`mcp_tool_call`, which names `server` and `tool`), with the reasoning item the runner
skips. The recorded turns are events.jsonl and events-resume.jsonl (Claude, 21 Nov 2026, which
have no tool items); a real turn with tools is recorded at 27.7's e2e, where a hub and a
dispatcher are started anyway.
