"""Parameterized SQL statements used by notice storage writes."""

NOTICE_SELECT_SQL = """
SELECT duid, category_id, category_name, title, posted_date, updated_date,
       department, source_url, body, body_expires_at, content_hash,
       attachments_present, collected_at, tombstone_at, source_status
FROM notices WHERE duid = ?
"""

NOTICE_UPSERT_SQL = """
INSERT INTO notices(
    duid, category_id, category_name, title, posted_date, updated_date,
    department, source_url, body, body_expires_at, content_hash,
    attachments_present, collected_at, tombstone_at, source_status
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(duid) DO UPDATE SET
    category_id=excluded.category_id, category_name=excluded.category_name,
    title=excluded.title, posted_date=excluded.posted_date,
    updated_date=excluded.updated_date, department=excluded.department,
    source_url=excluded.source_url, body=excluded.body,
    body_expires_at=excluded.body_expires_at, content_hash=excluded.content_hash,
    attachments_present=excluded.attachments_present,
    collected_at=excluded.collected_at, tombstone_at=excluded.tombstone_at,
    source_status=excluded.source_status
"""

REVISION_INSERT_SQL = """
INSERT INTO notice_revisions(
    duid, content_hash, category_id, category_name, title, posted_date,
    updated_date, department, source_url, attachments_present, collected_at,
    changed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
