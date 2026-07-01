"""Schema version 2: projects_master + disclosure_events."""

SCHEMA_VERSION = 2

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    company TEXT DEFAULT '',
    approval_number TEXT DEFAULT '',
    location TEXT DEFAULT '',
    district TEXT DEFAULT '',
    st_eia_id TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_master_approval
    ON projects_master(approval_number)
    WHERE approval_number != '';

CREATE TABLE IF NOT EXISTS disclosure_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    disclosure_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    lifecycle_stage TEXT DEFAULT '',
    event_date TEXT DEFAULT '',
    title TEXT DEFAULT '',
    summary_json TEXT DEFAULT '{}',
    source_url TEXT DEFAULT '',
    page_no INTEGER DEFAULT 1,
    synced_at TEXT NOT NULL,
    UNIQUE(source, disclosure_type, external_id),
    FOREIGN KEY(master_id) REFERENCES projects_master(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_master ON disclosure_events(master_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON disclosure_events(disclosure_type);
CREATE INDEX IF NOT EXISTS idx_events_date ON disclosure_events(event_date);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    file_type TEXT NOT NULL,
    file_name TEXT DEFAULT '',
    file_url TEXT DEFAULT '',
    file_external_id TEXT DEFAULT '',
    download_status TEXT DEFAULT 'direct',
    UNIQUE(event_id, file_type, file_url),
    FOREIGN KEY(event_id) REFERENCES disclosure_events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS master_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_id INTEGER NOT NULL,
    alias_type TEXT NOT NULL,
    alias_value TEXT NOT NULL,
    UNIQUE(alias_type, alias_value),
    FOREIGN KEY(master_id) REFERENCES projects_master(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    trigger_mode TEXT NOT NULL,
    message TEXT DEFAULT '',
    stats_json TEXT DEFAULT '{}'
);

CREATE VIRTUAL TABLE IF NOT EXISTS masters_fts USING fts5(
    canonical_name,
    company,
    approval_number,
    location,
    district,
    content='projects_master',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS masters_ai AFTER INSERT ON projects_master BEGIN
    INSERT INTO masters_fts(rowid, canonical_name, company, approval_number, location, district)
    VALUES (new.id, new.canonical_name, new.company, new.approval_number, new.location, new.district);
END;

CREATE TRIGGER IF NOT EXISTS masters_ad AFTER DELETE ON projects_master BEGIN
    INSERT INTO masters_fts(masters_fts, rowid, canonical_name, company, approval_number, location, district)
    VALUES ('delete', old.id, old.canonical_name, old.company, old.approval_number, old.location, old.district);
END;

CREATE TRIGGER IF NOT EXISTS masters_au AFTER UPDATE ON projects_master BEGIN
    INSERT INTO masters_fts(masters_fts, rowid, canonical_name, company, approval_number, location, district)
    VALUES ('delete', old.id, old.canonical_name, old.company, old.approval_number, old.location, old.district);
    INSERT INTO masters_fts(rowid, canonical_name, company, approval_number, location, district)
    VALUES (new.id, new.canonical_name, new.company, new.approval_number, new.location, new.district);
END;
"""

FTS_REBUILD_SQL = """
INSERT INTO masters_fts(masters_fts) VALUES('rebuild');
INSERT INTO masters_fts(masters_fts) VALUES('delete-all');
INSERT INTO masters_fts(rowid, canonical_name, company, approval_number, location, district)
SELECT id, canonical_name, company, approval_number, location, district FROM projects_master;
"""
