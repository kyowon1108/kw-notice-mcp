# Operations

Running the collector and server day to day: the scheduler contract, database
care, and MCP client configuration.

## Configuration

Settings come from `KW_NOTICE_*` environment variables, and CLI options override
them. Copy `.env.example` to `.env` only if you need local overrides — it holds
no credentials or secret-like values, and there is no robots-bypass setting to
find.

| variable | default | bounds |
|---|---|---|
| `KW_NOTICE_DB_PATH` | `data/kw-notice.sqlite3` | not a directory or symlink |
| `KW_NOTICE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `KW_NOTICE_USER_AGENT` | `kw-notice-mcp/0.1 (+local metadata collector)` | 8–200 chars, no control whitespace |
| `KW_NOTICE_MAX_PAGES` | `1` | 1–50 |
| `KW_NOTICE_MAX_DETAIL_REQUESTS` | `100` | 1–100 |
| `KW_NOTICE_MAX_DURATION_SECONDS` | `600` | 1–600 |

## The scheduler contract

Freshness is the operator's responsibility. This repository intentionally ships
no scheduler, service unit, or container — the scheduler lives outside it and
drives the same bounded entry point:

```sh
uv run kw-notice-mcp crawl --metadata-only --db-path data/kw-notice.sqlite3
```

The reference deployment is a launchd agent firing every 10 minutes on weekdays
between 09:00 and 18:59.

**A scheduler is only safe if it reacts to the exit code rather than retrying on
a fixed timer:**

| exit | required behaviour |
|---|---|
| `0` | success — nothing to do |
| `10` | blocked or budget-exhausted. Trigger an **escalating cool-down** that suppresses further requests. Never retry immediately. |
| `11` | busy — another crawl holds the lease. Skip the tick silently. |
| `12` | invalid configuration. Retrying cannot fix it; it needs a human. |
| `13` | infrastructure failure. Surface it; do not treat as transient forever. |

Two further rules:

- **Confine it to a crawl window** rather than polling around the clock. The
  university publishes on weekday business hours; overnight polling adds request
  volume for nothing.
- **Log only the safe `block_reason`** the collector already emits. Do not log
  response bodies, and do not add your own retry layer on top of the collector's.

Do not run a second scheduler in parallel. Two schedulers crawl the source twice
for identical data — this is exactly why the repository has only one collection
path and no in-repo cron.

### Known gap: no heartbeat

Nothing in this repository alarms when the scheduler stops. A dead scheduler and
a quiet weekend produce the same `status` output for the first 24 hours, after
which notices merely decay from `fresh` to `stale`. Detecting that requires
running `status` and reading it, or building an external heartbeat. This is a
real gap, not a design choice.

Cached notices age against their `collected_at` timestamp:

| freshness | age |
|---|---|
| `fresh` | ≤ 24 hours |
| `stale` | ≤ 7 days |
| `expired` | older, or no successful crawl on record |

`status` reports these counts over a sample of the 50 most recent notices.

## Checking state

```sh
uv run kw-notice-mcp status --db-path data/kw-notice.sqlite3
```

```
db=data/kw-notice.sqlite3 notices=79 freshness=fresh:47,stale:3,expired:0 \
  fts5=available crawl=success run_id=b571d03b… pages=1 details=0 block_reason=-
```

`crawl=` is the most recent run's outcome and `block_reason=` explains a blocked
one. `status` opens only the local database; it never contacts the source.

## Database care

The SQLite file holds bounded, redacted metadata only. Metadata-only runs keep
`body` as `NULL` and add no body tokens to FTS. Raw HTML, attachments, email
addresses, and phone numbers are never stored. Every notice keeps a constructed
source link back to the original page.

Keep the database local and restrict it to the operator:

```sh
chmod 600 data/kw-notice.sqlite3
sqlite3 data/kw-notice.sqlite3 '.backup data/kw-notice.backup.sqlite3'
sqlite3 data/kw-notice.sqlite3 'PRAGMA integrity_check;'
```

Restore only while the server and collector are stopped, after checking the
backup path and permissions:

```sh
cp data/kw-notice.backup.sqlite3 data/kw-notice.sqlite3
chmod 600 data/kw-notice.sqlite3
```

The MCP server needs no restart when the database changes underneath it. Every
tool call opens SQLite fresh in URI `mode=ro`, so a completed refresh is visible
to the very next call.

## MCP client configuration

Any MCP client that can spawn a local STDIO server works. The example below is
the Hermes `mcp_servers` form; the JSON is also valid YAML for a generic
`~/.hermes/config.yaml` entry. Replace both paths with operator-owned ones. It
contains no token, credential, or secret, and no Hermes- or Discord-specific
runtime dependency:

```json
{
  "mcp_servers": {
    "kw-notice": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/path/to/kw-notice-mcp",
        "kw-notice-mcp",
        "serve",
        "--db-path",
        "/path/to/kw-notice-mcp/data/kw-notice.sqlite3"
      ]
    }
  }
}
```

`serve` reserves stdout for MCP JSON-RPC, sends logs to stderr, and exits when
stdin closes — so the server's lifetime is the client's lifetime.

## Local checks

```sh
uv run pytest -q
uv run basedpyright
uv run ruff check
uv run ruff format --check
uv run python scripts/check_no_excuse_rules.py src tests
```

These are the same gates CI runs. No test contacts the live site; see
[collector-policy.md](collector-policy.md).
