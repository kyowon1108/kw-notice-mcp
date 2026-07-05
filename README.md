# kw-notice-mcp

광운대학교 공지를 수집해 로컬 SQLite에 캐시하고, 읽기 전용 MCP 도구 4개로 STDIO에 제공하는 서버입니다.

## 제공 기능

MCP 도구 4개입니다. 전부 읽기 전용이고, 호출마다 SQLite를 URI `mode=ro` 로 새로 엽니다.

| 도구 | 입력 | 반환 |
|---|---|---|
| `list_categories` | — | 고정 카테고리 목록과 캐시 건수 |
| `list_latest_notices` | `category?`, `limit`, `offset` | 게시일 기준 최신 공지 |
| `search_notices` | `query`, `category?`, `published_from?`, `published_to?`, `limit`, `offset` | FTS5 검색 |
| `get_notice` | `duid` | 단건 조회 |

## 기술 스택

- 런타임: Python 3.13, `uv`
- 서버 · 저장: MCP SDK (STDIO), SQLite + FTS5
- 수집: httpx2, BeautifulSoup4
- 설정 · CLI: Pydantic, pydantic-settings, Typer
- 품질: pytest, ruff, basedpyright

## 로컬 실행

```sh
uv sync
uv run kw-notice-mcp --help
uv run kw-notice-mcp init-db --db-path data/kw-notice.sqlite3
uv run kw-notice-mcp status  --db-path data/kw-notice.sqlite3
uv run kw-notice-mcp serve   --db-path data/kw-notice.sqlite3
```

설정은 `KW_NOTICE_*` 환경변수로 읽고, CLI 옵션이 우선합니다. 자격증명이나 비밀값은 없습니다.

MCP 클라이언트 등록 예시입니다.

```json
{
  "mcpServers": {
    "kw-notice": {
      "command": "/Users/me/.local/bin/uv",
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

`command` 에는 `uv` 의 절대경로를 적어야 합니다. Claude Desktop 같은 GUI 앱은 셸 PATH를 상속하지 않아서 `"uv"` 로만 적으면 프로세스 생성이 실패합니다.

## 수집 정책

- robots 우회 없음. 우회 스위치 자체가 없습니다.
- 실행당 요청 1회 예산. 목록 첫 페이지만 봅니다.
- metadata-only. 상세 페이지, 본문, 첨부, 이미지, 이메일, 전화번호는 절대 요청하지 않습니다.
- fail-closed. 403, 429, CAPTCHA, 깨진 마크업, 타임아웃이면 즉시 중단합니다.
- 권한을 주장하지 않습니다. 이 통제들은 통제일 뿐 허가가 아닙니다.

종료 코드가 스케줄러 연동 계약입니다. `0` 성공, `10` 차단 또는 예산 소진, `11` 다른 크롤이 lease 보유, `12` 설정 오류, `13` 인프라 실패.
