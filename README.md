# InvenTree MCP Server

MCP server for [InvenTree](https://inventree.org) inventory management. Provides 12 parameterized tools covering 117 operations for parts, stock, build orders, purchase/sales/return orders, companies, barcodes, labels, reports, attachments, and system administration.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An InvenTree instance with API access enabled
- An API token (generate from InvenTree > Settings > API Tokens)

## Setup

```bash
# Clone the repository
git clone https://github.com/puran-water/inventree-mcp.git
cd inventree-mcp

# Copy the example environment file and fill in your values
cp .env.example .env

# Install dependencies
uv sync
```

Edit `.env` with your InvenTree instance URL and API token:

```
INVENTREE_URL=https://your-inventree-instance.example.com
INVENTREE_TOKEN=your-api-token-here
```

File operations (attachment upload/download, label/report template download) are confined to
`INVENTREE_MCP_FILES` (default `~/inventree-mcp-files`). Paths are resolved relative to it, and
anything outside it is rejected. Downloads never overwrite existing files.

## Usage

### STDIO mode (default)

```bash
uv run python server.py
```

### SSE mode (HTTP transport)

```bash
uv run python server.py sse --port 3074
```

### Any machine, straight from GitHub (no clone)

Needs [uv](https://docs.astral.sh/uv/). Put the credentials in a per-machine config file:

```bash
mkdir -p ~/.config/inventree-mcp
cat > ~/.config/inventree-mcp/.env <<'EOF'
INVENTREE_URL=https://your-inventree-instance.example.com
INVENTREE_TOKEN=your-api-token-here
EOF
chmod 600 ~/.config/inventree-mcp/.env
```

Register it for every Claude Code session on that machine:

```bash
claude mcp add --scope user inventree -- \
  uvx --from git+https://github.com/TheFern2/inventree-mcp inventree-mcp
```

uvx caches the install; run `uv cache clean inventree-mcp` after pushing changes, then restart the
Claude session to pick them up.

Settings are read from real environment variables first, then a `.env` next to `server.py`,
then `~/.config/inventree-mcp/.env` (override the location with `INVENTREE_MCP_ENV`).

### Claude Desktop / MCP client configuration

Add to your MCP client config (e.g. `~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "inventree-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/inventree-mcp", "python", "server.py"],
      "env": {
        "INVENTREE_URL": "https://your-inventree-instance.example.com",
        "INVENTREE_TOKEN": "your-api-token-here"
      }
    }
  }
}
```

Or for SSE transport:

```json
{
  "mcpServers": {
    "inventree-mcp": {
      "url": "http://localhost:3074/sse"
    }
  }
}
```

## Tools

Each tool uses a parameterized `operation` field to select the specific action.

| Tool | Operations | Description |
|------|-----------|-------------|
| `part` | 21 | Part & category management (list, get, create, update, delete, BOM, suppliers, parameters) |
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
| `system` | 8 | Health, version, settings, users, groups, currencies |

## Architecture

- **`server.py`** — FastMCP server with 12 parameterized tools and dual transport (STDIO/SSE)
- **`client.py`** — Async adapter wrapping the official [inventree-python](https://github.com/inventree/inventree-python) library via `asyncio.to_thread()` with a semaphore for concurrency control

The `inventree-python` library is synchronous (requests-based). All calls are offloaded to threads to maintain async compatibility with the MCP framework.

## License

MIT
