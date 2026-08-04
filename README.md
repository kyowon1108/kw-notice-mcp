# kw-notice-mcp

Local, read-only Kwangwoon University notice tooling. The collector is
robots-first, bounded, metadata-minimizing, and exposes only cached results
through a local MCP STDIO server.

## Install and run

This project uses Python 3.13+ and `uv`:

```sh
uv sync
uv run kw-notice-mcp --help
uv run kw-notice-mcp init-db --db-path data/kw-notice.sqlite3
uv run kw-notice-mcp status --db-path data/kw-notice.sqlite3
```

Configuration is read from `KW_NOTICE_*` environment variables. Copy
`.env.example` to `.env` only if you need local overrides; it contains no
credentials or secret-like values. The crawl command always uses the
metadata-only operational mode: one robots request followed by the first
`전체` list page. It stores only DUID, canonical category, redacted/capped
title, posted/updated dates, department, constructed source URL, collection
time, and source status. It never requests detail pages, body text,
attachments, images, email addresses, or phone numbers, and it never uses a
generic robots bypass.

The command exit codes are stable: `0` success, `10` blocked or budget
exhausted, `11` busy because another crawl owns the SQLite lease, `12` invalid
configuration, and `13` infrastructure failure.

## Commands

```sh
uv run kw-notice-mcp init-db --db-path data/kw-notice.sqlite3
uv run kw-notice-mcp crawl --metadata-only --db-path data/kw-notice.sqlite3
uv run kw-notice-mcp status --db-path data/kw-notice.sqlite3
uv run kw-notice-mcp serve --db-path data/kw-notice.sqlite3
```

`crawl --metadata-only` checks only `https://www.kw.ac.kr/robots.txt` before
requesting the first `전체` index page. It uses the bounded collector one
request at a time, with SQLite `BEGIN IMMEDIATE` locking and stale-run
recovery. A blocked run may update only `crawl_runs`; notices, FTS rows, and
revisions remain unchanged. Only the complete known KW HTTP-200 custom-404
fingerprint may proceed in metadata-only mode. Same-title HTML, any content
mutation, generic/non-200 HTTP 404, arbitrary HTML, malformed robots text,
explicit denial, 403, 429, 5xx, and CAPTCHA remain blocked.
Logs are JSON records on stderr with run ID, page/detail counters, status, and
a safe block reason. Response bodies and personal data are never logged.
`serve` keeps stdout reserved for MCP JSON-RPC and exits when stdin closes.

## Current source-policy state

During the 2026-08-05 inspection, `/robots.txt` returned HTTP 200 with a
captured HTML custom-404 document titled
`HTTP 404 요청하신 페이지가 존재하지 않습니다.`. Authorization is not based
on that title: the classifier requires `text/html`, HTTP 200, the exact
LF-canonical byte length, and the pinned SHA-256 of the complete captured
document. Only that fingerprint becomes `robots_missing` and permits page-1
metadata collection. Any byte/content change and every non-200 response fail
closed. Tests use the captured local fixture and fake responses; verification
never performs a live crawl.

The GitHub Actions refresh schedule is weekdays, 09:00–18:00 KST, at 15-minute
offsets `07,22,37,52` (`7,22,37,52 0-8 * * 1-5` UTC). GitHub schedules are
best-effort and may start late, so the workflow remains one-concurrent and
lease-protected. Actions is the collector runtime; the MCP remains local
STDIO. The workflow downloads the prior manifest, database, and checksum,
verifies the complete generation before reuse, and initializes only when the
notice-protocol assets are truly absent. After a successful metadata-only
crawl it uploads immutable generation assets and updates the stable
`data-latest` manifest last. Blocked, busy, invalid, incomplete, checksum, and
infrastructure outcomes publish nothing.
On 403, 429, or CAPTCHA, the run stops immediately and the operator should
cool down before the next selected slot. The older `kw-service` repositories
are architectural precedent only, not an upstream dependency.

## Local database operations

The SQLite file contains bounded, redacted metadata only on the CLI path;
metadata-only runs keep body `NULL` and add no body tokens to FTS. Raw HTML,
attachments, email addresses, and phone numbers are not stored. Every notice
retains a constructed source link for the original page. Keep the database local and
restrict it to the operator, for example:

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

Scheduling is operator-owned and intentionally out of scope. This repository
does not implement a second cron/systemd/Docker scheduler, a remote HTTP
server, OAuth, or a public deployment.

## Release DB consumption

The durable handoff is the stable GitHub Release tag `data-latest`, not an
Actions artifact. Its single authoritative pointer is the Release body, a
strict JSON value naming one immutable, SHA-256-addressed manifest asset. That
manifest names the immutable database asset and its checksum asset. The three
immutable assets are uploaded and verified before the Release body is edited;
there is no mutable pointer asset or pointer-asset `--clobber` window. GitHub
Release does not provide a global multi-asset transaction: a failed or partial
asset upload remains unreachable, while a failed later body edit preserves the
prior pointer (or is resolved by read-back). An initial body-edit failure leaves
no authoritative generation. Consumers keep their current local DB until the
strict pointer and complete generation verify.

Resolve the stable Release body first, parse its strict pointer, then download
the exact immutable manifest and the two assets it declares into a temporary
directory. The repository helper requires all files, verifies the checksum,
then revalidates the copied SQLite candidate for integrity, the exact expected
schema, and zero retained body content before atomically replacing the local
SQLite file:

```bash
release_dir=$(mktemp -d)
gh release view data-latest --repo kyowon1108/kw-notice-mcp --json body,assets \
  >"$release_dir/release.json"
jq -r '.body // empty' "$release_dir/release.json" \
  >"$release_dir/release-pointer.json"
uv run python -m kw_notice_mcp.release verify-pointer \
  --pointer "$release_dir/release-pointer.json" \
  >"$release_dir/validated-pointer.json"
manifest_asset=$(jq -r '.manifest_asset' "$release_dir/validated-pointer.json")
gh release download data-latest --repo kyowon1108/kw-notice-mcp \
  --pattern "$manifest_asset" --dir "$release_dir"
uv run python -m kw_notice_mcp.release verify-manifest \
  --manifest "$release_dir/$manifest_asset" >"$release_dir/validated.json"
database_asset=$(jq -r '.database_asset' "$release_dir/validated.json")
checksum_asset=$(jq -r '.checksum_asset' "$release_dir/validated.json")
gh release download data-latest --repo kyowon1108/kw-notice-mcp \
  --pattern "$database_asset" --dir "$release_dir"
gh release download data-latest --repo kyowon1108/kw-notice-mcp \
  --pattern "$checksum_asset" --dir "$release_dir"
uv run python -m kw_notice_mcp.release restore \
  --manifest "$release_dir/$manifest_asset" \
  --assets-dir "$release_dir" \
  --database ./notices.sqlite3
rm -rf "$release_dir"
uv run kw-notice-mcp serve --db-path ./notices.sqlite3
```

Local producer staging has a separate, precise atomicity boundary: DB,
checksum, and manifest are built and verified in a temporary generation
directory, then one same-filesystem directory rename exposes
`generations/<sha256>`. A failure before that rename exposes no new manifest or
resolvable generation pair. Consumer installation similarly verifies the
complete downloaded pair before one filesystem replacement of the local DB. A
missing Release or a Release with no notice-protocol assets may initialize
safely; an empty/invalid pointer, incomplete pair, checksum mismatch, API
failure, or download failure stops the refresh before crawl or publication and
leaves the prior Release untouched.

The schedule can be delayed or coalesced by GitHub Actions. The rule-portal
search report found no explicit crawling rule in that limited search, but it
did not establish permission or settle legal, terms-of-use, privacy, or
redistribution questions. Operators should obtain written confirmation before
describing automated access as authorized; the collector therefore remains
bounded, metadata-only, robots-aware, and fail-closed.

Deployment operators acknowledge that these safeguards and the workflow's
bounded GitHub token permissions are controls, not a claim of authorization.
They are responsible for confirming access and redistribution approval,
protecting the local database and Release, and reviewing failed or blocked
runs. This responsibility does not disable the user-approved weekday refresh
schedule; it governs its operation.

## Fixture-only tests and quality checks

No required test contacts the live site. Permissive robots, HTML pages, and
transport failures are injected in memory or read from synthetic fixtures:

```sh
uv run pytest tests/integration/test_cli.py -q
uv run pytest -q
uv run basedpyright
uv run ruff check
uv run ruff format --check
uv run python scripts/check_no_excuse_rules.py src tests
```

## Generic Hermes STDIO configuration

Hermes can spawn local MCP servers from its `mcp_servers` configuration. The
following JSON is also valid YAML syntax for a generic `~/.hermes/config.yaml`
entry; replace the project and database paths with operator-owned paths. It
contains no token, credential, secret, or Hermes/Discord runtime dependency:

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

Hermes and Discord remain external consumers. This repository owns only the
notice cache and four read-only MCP tools; it does not receive Discord events,
hold platform credentials, or route messages.
