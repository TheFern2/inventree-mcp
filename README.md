# InvenTree MCP Server

MCP server for [InvenTree](https://inventree.org) inventory management. Provides 12 parameterized tools covering 122 operations for parts, stock, build orders, purchase/sales/return orders, companies, barcodes, labels, reports, attachments, and system administration.

Tested against InvenTree **1.6.1** (API version 561) with `inventree-python` 0.25.0 and `mcp` 1.26.

## Requirements

- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- An InvenTree instance with API access enabled
- An API token for a dedicated InvenTree user (see [InvenTree user and permissions](#inventree-user-and-permissions))

## Setup

The server is a plain API client: run it on any machine that can reach your InvenTree server.
Nothing is installed on the InvenTree side.

### 1. Credentials

Put the URL and token in a per-machine config file:

```bash
mkdir -p ~/.config/inventree-mcp
cat > ~/.config/inventree-mcp/.env <<'EOF'
INVENTREE_URL=https://your-inventree-instance.example.com
INVENTREE_TOKEN=your-api-token-here
EOF
chmod 600 ~/.config/inventree-mcp/.env
```

Settings are read from real environment variables first, then a `.env` next to `server.py`
(when running from a clone), then `~/.config/inventree-mcp/.env`. Point `INVENTREE_MCP_ENV` at a
different file to override the last location.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `INVENTREE_URL` | yes | | InvenTree base URL, e.g. `http://192.168.0.2` |
| `INVENTREE_TOKEN` | yes | | API token of the MCP user |
| `INVENTREE_MCP_FILES` | no | `~/inventree-mcp-files` | Directory that file operations are confined to |
| `INVENTREE_MCP_ENV` | no | `~/.config/inventree-mcp/.env` | Location of the config file |
| `LOG_LEVEL` | no | `INFO` | Python log level (logs go to stderr) |

### 2. Register with Claude Code

Straight from GitHub, available in every Claude Code session on the machine:

```bash
claude mcp add --scope user inventree -- \
  uvx --from git+https://github.com/TheFern2/inventree-mcp inventree-mcp
```

Without `--scope user` the server is only registered for the directory you ran the command in.
Check it with `claude mcp list` or `/mcp` inside a session.

uvx caches the install. To pick up new commits, run `uv cache clean inventree-mcp` and start a new
session.

### From a clone (for development)

```bash
git clone https://github.com/TheFern2/inventree-mcp.git
cd inventree-mcp
uv sync
claude mcp add --scope user inventree -- \
  "$PWD/.venv/bin/python" "$PWD/server.py"
```

A `.env` in the clone (see `.env.example`) takes precedence over `~/.config/inventree-mcp/.env`.
Code changes take effect on the next session.

### Other MCP clients

Claude Desktop and other clients take the same command. For example in
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "inventree": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/TheFern2/inventree-mcp", "inventree-mcp"]
    }
  }
}
```

Credentials still come from `~/.config/inventree-mcp/.env`. Note that the
[delete confirmation hook](#confirming-deletes) only exists in Claude Code; other clients delete
without asking if the token has delete rights.

An SSE transport is also available (`inventree-mcp sse 3074`, then connect to
`http://localhost:3074/sse`). It binds to `127.0.0.1` with no authentication; prefer stdio.

## InvenTree user and permissions

Don't use your personal or an admin account. Create a dedicated user so the token's rights are
limited and its actions are attributable (`creation_user` on records it creates):

1. **Admin → Groups**: create a group, e.g. `mcp`. Give it **View / Add / Change** on the roles it
   needs (Part, Part Categories, Stock, Stock Locations, BOM, Build, Purchase, Sales, Return).
   Decide on **Delete** (see below). Leave **Admin** off unless the agent should be able to change
   server settings.
2. **Admin → Users**: create `mcp`, not staff, not superuser, member of that group.
3. Log in as `mcp` → user menu → **Account settings → Access Tokens** → generate a token, and put
   it in `~/.config/inventree-mcp/.env`.

Treat the token like a password. If it is ever exposed, revoke it in InvenTree and generate a new
one (a new token does not invalidate the old one).

### Confirming deletes

The `part`, `company` and `attachment` tools expose a `delete` operation. Two ways to keep it safe:

- **No delete at all**: leave Delete off for the group. Delete calls fail with 403 and deletes are
  done in the InvenTree web UI.
- **Delete with confirmation**: turn Delete on for the group and add this hook to
  `~/.claude/settings.json` on each machine. Claude Code then shows an approve/deny prompt with the
  tool and its arguments before any delete runs; every other operation is unaffected.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__inventree__.*",
        "hooks": [
          {
            "type": "command",
            "command": "jq -c 'if .tool_input.operation == \"delete\" then {hookSpecificOutput: {hookEventName: \"PreToolUse\", permissionDecision: \"ask\", permissionDecisionReason: (\"InvenTree DELETE via \" + .tool_name + \": \" + (.tool_input | tostring))}} else empty end'",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Merge it into any existing `hooks` block rather than replacing it. Needs `jq` (preinstalled on
macOS as `/usr/bin/jq`). The matcher assumes the server is registered as `inventree`; adjust
`mcp__inventree__` if you used another name. Run `/hooks` to confirm it is loaded.

The hook guards against the agent deleting things by mistake (or because of instructions planted
in a part's notes). It is not a security boundary: it only runs in Claude Code, on machines where
you added it, and anything that can read the `.env` file has the token.

## Using it

Ask in plain language ("list parts in the Fasteners category", "how much stock of M3 screws do we
have", "create a purchase order for…"). Things worth knowing:

- **Token changes need a reconnect.** The server connects on the first tool call and keeps that
  connection. After editing the `.env`, run `/mcp` → `inventree` → **Reconnect**, or start a new
  session.
- **Parameters** (Resistance, Capacitance, Package…): set up templates before entering parts.
  `part.create_parameter_template` → `part.add_category_parameter` (attaches a template to a
  category with an optional default, so new parts in it get the parameter) →
  `part.set_parameter` on each part. Templates are global in InvenTree 1.x, shared across models.
  Creating or editing templates requires a **staff** user (InvenTree checks `is_staff`, not group
  roles), so the `mcp` user gets 403. Either create templates in the admin UI, or turn on *Is
  Administrator* for `mcp` while setting them up and turn it off afterwards. Attaching templates to
  categories (Part Categories: Add) and setting values (Parts: Change) work without staff.
- **Deleting parts:** InvenTree refuses to delete an active part. Mark it inactive first
  (`part.update` with `{"active": false}`), then delete.
- **Categories and locations can't be deleted** through the MCP; use the web UI.
- **File operations** (attachment upload/download, label/report template download) only work
  inside `INVENTREE_MCP_FILES`. Paths are relative to it, anything resolving outside it (including
  via `..` or symlinks) is rejected, and downloads never overwrite existing files. To attach a file,
  copy it into that directory first.
- **Errors** are returned to the agent as `{"error": ..., "hint": ...}` with the auth token
  redacted.
- **`label.list_templates`** filters by `label_type`, which defaults to `part`.
- **Label/report printing** was fixed for the InvenTree 1.x print API but has not been tested
  against a live printer yet.

## Tools

Each tool uses a parameterized `operation` field to select the specific action.

| Tool | Operations | Description |
|------|-----------|-------------|
| `part` | 26 | Part & category management (list, get, create, update, delete, BOM, suppliers, parameter templates and values) |
| `stock` | 16 | Stock item & location management (list, get, create, transfer, count, add, remove) |
| `build_order` | 9 | Manufacturing build orders (list, get, create, update, allocate, complete, cancel) |
| `purchase_order` | 12 | Purchase order lifecycle (list, get, create, update, issue, receive, complete) |
| `sales_order` | 14 | Sales order lifecycle (list, get, create, shipments, allocations) |
| `return_order` | 8 | Return order management |
| `company` | 12 | Suppliers, manufacturers, customers, contacts, addresses |
| `barcode` | 4 | Barcode scan, assign, unassign, lookup |
| `label` | 5 | Label template listing and printing |
| `report` | 3 | Report template listing and generation |
| `attachment` | 5 | File attachments on any object (upload, download, delete) |
| `system` | 9 | Health, version, settings, users, groups, owners, project codes, currencies |

## Architecture

- **`server.py`** — FastMCP server with 12 parameterized tools and dual transport (STDIO/SSE)
- **`client.py`** — Async adapter wrapping the official [inventree-python](https://github.com/inventree/inventree-python) library via `asyncio.to_thread()` with a semaphore for concurrency control

The `inventree-python` library is synchronous (requests-based). All calls are offloaded to threads to maintain async compatibility with the MCP framework.

## License

MIT
