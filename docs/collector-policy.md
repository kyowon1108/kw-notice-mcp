# Collector policy

The long-form version of the policy summarized in the README. It covers what the
collector requests, what it stores, how it fails, and what this project does and
does not claim about authorization.

## Position on authorization

**This project does not claim authorization to collect from the source.**

A search of the university's rule portal found no explicit rule governing
automated access. That search was limited, and a null result is not permission.
It does not settle terms of use, privacy law, copyright, or redistribution.

Every safeguard below is a *control*, adopted so that the collector stays small
and reversible while the authorization question is open. Controls are not a
substitute for permission. An operator who wants to describe automated access as
authorized should obtain written confirmation first.

## Request policy

The operational CLI path is metadata-only. On a successful run it issues exactly
one request, to the first `전체` list page:

```
https://www.kw.ac.kr/ko/life/notice.jsp?srCategoryId=&mode=list&searchKey=1&searchVal=&tpage=1
```

- **No `/robots.txt` request on this path.** The metadata-only path does not
  fetch robots at all, so there is no robots response to interpret and no generic
  bypass switch to set. This is the operator-directed policy for the CLI; it is
  not a claim of permission.
- **The internal FULL path keeps a strict robots parser** for callers that select
  it explicitly. A missing robots resource blocks a FULL run outright
  (`robots_missing_metadata_only_required`).
- URLs are constructed, never taken from markup. `src/kw_notice_mcp/source.py`
  builds the exact allowlisted list and detail queries and re-validates any URL
  against host, scheme, port, path, and an exact query shape. Arbitrary hrefs
  found in the page are rejected.
- One request at a time, minimum 2 s delay per attempt, at most 3 attempts per
  logical request, at most 5 redirect hops, 4 MiB response cap, and a global
  per-run wire budget.

## What is never fetched

The metadata-only path never requests:

- notice detail pages
- body text
- attachments or images
- email addresses or phone numbers

`detail_requests` has been `0` on every run to date.

## What is stored

Per notice: DUID, canonical category id and name, redacted and length-capped
title, posted date, updated date, department, constructed source URL, collection
time, and source status. Metadata-only runs leave `body` as `NULL` and add no
body tokens to the FTS index.

Raw HTML is never persisted. Response bodies are decoded transiently and
discarded.

### Redaction

`src/kw_notice_mcp/redaction.py` applies a versioned (`v1`) deterministic pass to
every human-authored field before it is stored, replacing matches with explicit
markers:

| pattern | marker |
|---|---|
| email addresses | `[REDACTED_EMAIL]` |
| Korean mobile, landline, and service numbers; international numbers | `[REDACTED_PHONE]` |
| resident registration numbers | `[REDACTED_RESIDENT_ID]` |
| labelled identifiers (학번 / 사번 / 계좌 / 주민등록번호) | `[REDACTED_IDENTIFIER]` |
| bare 10–14 digit account-like runs | `[REDACTED_ACCOUNT]` |

Fields are then capped: title 500, category name 64, department 200, body 4000
characters. The account-run rule has one deliberate exception — a run preceded by
`공지` or `notice` is treated as a notice number and left intact.

## Fail-closed contract

A run stops and records a blocked outcome on any of:

| condition | reason |
|---|---|
| HTTP 403 | `forbidden` |
| HTTP 429 | `rate_limit` |
| other non-2xx | `http_failure` |
| CAPTCHA / WAF challenge marker in the body | `captcha` |
| malformed markup, wrong content type, non-UTF-8 body, any parse issue | `markup` |
| redirect off-host, to a disallowed path, or without a target | `cross_host_redirect`, `disallowed_redirect_path`, `invalid_redirect_target` |
| more than 5 redirect hops | `redirect_hop_cap` |
| connection failure or timeout after 3 attempts | `transport_failure` |
| response over 4 MiB | `oversized` |
| wall-clock or request budget exhausted | `time_budget`, `wire_budget`, `detail_budget` |

A blocked run may update the `crawl_runs` row **only**. Notices, FTS rows, and
revisions are left exactly as they were. There is no partial commit: the
metadata batch is written once, after the page parses cleanly.

On 403, 429, or CAPTCHA the run stops immediately and the operator must cool down
before retrying. The repository does not implement that cool-down — enforcing it
is the scheduler's job. See [operations.md](operations.md).

> These paths have been exercised only by adversarial tests against injected
> responses. As of the measurement window in the README, all 24 real runs
> succeeded, so none of the table above has fired against the live source.

## Exit codes

| code | meaning |
|---|---|
| `0` | success |
| `10` | blocked, or budget exhausted |
| `11` | busy — another crawl holds the SQLite lease |
| `12` | invalid configuration |
| `13` | infrastructure failure |

These are stable and are the intended integration surface for a scheduler.

## Logging

Logs are JSON records on stderr carrying run ID, page and detail counters,
status, and a bounded `block_reason`. Response bodies, notice content, and
personal data are never logged. `serve` keeps stdout reserved for MCP JSON-RPC
and writes nothing else there.

## Scope boundaries

This repository owns the notice cache and the four read-only MCP tools. It does
not implement, and will not grow, a second scheduler, a remote HTTP server, OAuth
or any other auth flow, a container image, or a public deployment. It receives no
Discord events, holds no platform credentials, and routes no messages — those
belong to Hermes, which is an external consumer.

## Test posture

No required test contacts the live site. Robots policies, HTML pages, and
transport failures are injected in memory or read from synthetic fixtures under
`tests/fixtures/`, and `tests/security/test_fixture_safety.py` guards the
fixtures themselves. Verification never performs a live crawl.
