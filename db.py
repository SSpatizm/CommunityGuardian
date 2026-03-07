"""
Database module for Community Guardian.
Single-table SQLite persistence layer. Knows nothing about classification or HTTP.
"""

import os
import json
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "guardian.db")


def get_db():
    """Get a SQLite connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create the incidents table and indexes if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            TEXT NOT NULL,
            description      TEXT NOT NULL,
            location         TEXT NOT NULL,
            reported_at      TEXT NOT NULL,
            created_at       TEXT NOT NULL,

            category         TEXT NOT NULL DEFAULT 'uncategorized',
            severity         TEXT NOT NULL DEFAULT 'informational',
            confidence       REAL NOT NULL DEFAULT 0.0,
            action           TEXT NOT NULL DEFAULT '',
            classified_by    TEXT NOT NULL DEFAULT 'pending',
            matched_keywords TEXT DEFAULT NULL,
            fallback_reason  TEXT DEFAULT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_category    ON incidents(category);
        CREATE INDEX IF NOT EXISTS idx_severity    ON incidents(severity);
        CREATE INDEX IF NOT EXISTS idx_location    ON incidents(location);
        CREATE INDEX IF NOT EXISTS idx_reported_at ON incidents(reported_at);
    """)
    conn.commit()
    conn.close()


def now_iso():
    """Current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def insert_incident(data: dict) -> int:
    """
    Insert a fully classified incident row.
    Returns the new row ID.
    """
    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO incidents
           (title, description, location, reported_at, created_at,
            category, severity, confidence, action, classified_by,
            matched_keywords, fallback_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["title"],
            data["description"],
            data["location"],
            data["reported_at"],
            data["created_at"],
            data["category"],
            data["severity"],
            data["confidence"],
            data["action"],
            data["classified_by"],
            json.dumps(data["matched_keywords"]) if data.get("matched_keywords") else None,
            data.get("fallback_reason"),
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def update_incident(incident_id: int, data: dict):
    """Update user fields and reclassification results for an existing incident."""
    conn = get_db()
    conn.execute(
        """UPDATE incidents SET
            title=?, description=?, location=?,
            category=?, severity=?, confidence=?, action=?,
            classified_by=?, matched_keywords=?, fallback_reason=?
           WHERE id=?""",
        (
            data["title"],
            data["description"],
            data["location"],
            data["category"],
            data["severity"],
            data["confidence"],
            data["action"],
            data["classified_by"],
            json.dumps(data["matched_keywords"]) if data.get("matched_keywords") else None,
            data.get("fallback_reason"),
            incident_id,
        ),
    )
    conn.commit()
    conn.close()


def get_incident(incident_id: int) -> dict | None:
    """Fetch a single incident by ID. Returns dict or None."""
    conn = get_db()
    row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return _row_to_dict(row)


def query_incidents(category=None, severity=None, location=None, q=None, sort="newest") -> list[dict]:
    """Query incidents with optional filters. Returns list of dicts."""
    query = "SELECT * FROM incidents WHERE 1=1"
    params = []

    if category:
        query += " AND category = ?"
        params.append(category)
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    if location:
        query += " AND location LIKE ?"
        params.append(f"%{location}%")
    if q:
        query += " AND (title LIKE ? OR description LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])

    if sort == "oldest":
        query += " ORDER BY reported_at ASC"
    else:
        query += " ORDER BY reported_at DESC"

    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_incident_stats(hours=24) -> dict:
    """Get aggregate stats for recent incidents."""
    conn = get_db()
    cutoff = datetime.now(timezone.utc).isoformat()

    rows = conn.execute(
        """SELECT category, severity, COUNT(*) as count
           FROM incidents
           GROUP BY category, severity
           ORDER BY count DESC"""
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]

    by_category = {}
    by_severity = {"critical": 0, "moderate": 0, "informational": 0}
    for row in rows:
        cat = row["category"]
        sev = row["severity"]
        cnt = row["count"]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "critical": 0, "moderate": 0, "informational": 0}
        by_category[cat]["total"] += cnt
        by_category[cat][sev] += cnt
        by_severity[sev] += cnt

    conn.close()
    return {
        "total": total,
        "by_category": by_category,
        "by_severity": by_severity,
    }


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a regular dict with parsed matched_keywords."""
    d = dict(row)
    if d.get("matched_keywords"):
        try:
            d["matched_keywords"] = json.loads(d["matched_keywords"])
        except (json.JSONDecodeError, TypeError):
            d["matched_keywords"] = []
    else:
        d["matched_keywords"] = None
    return d
