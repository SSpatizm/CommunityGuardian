"""
Seed script for Community Guardian.
Loads sample_data.json, classifies each incident via the rule engine, and inserts into SQLite.
Idempotent: deletes existing data before seeding.
"""

import json
import os
import sys

from db import init_db, get_db, now_iso
from classifier import classify_report_rule_engine_only


def seed():
    """Load synthetic data and populate the database."""
    init_db()

    sample_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_data.json")
    if not os.path.exists(sample_path):
        print(f"Error: {sample_path} not found.")
        sys.exit(1)

    with open(sample_path) as f:
        incidents = json.load(f)

    conn = get_db()

    # Clear existing data for idempotent re-seeding
    conn.execute("DELETE FROM incidents")
    conn.commit()

    count = 0
    for inc in incidents:
        result = classify_report_rule_engine_only(
            inc["title"], inc["description"], inc["reported_at"]
        )
        conn.execute(
            """INSERT INTO incidents
               (title, description, location, reported_at, created_at,
                category, severity, confidence, action, classified_by,
                matched_keywords, fallback_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inc["title"],
                inc["description"],
                inc["location"],
                inc["reported_at"],
                now_iso(),
                result["category"],
                result["severity"],
                result["confidence"],
                result["action"],
                result["classified_by"],
                json.dumps(result["matched_keywords"]) if result["matched_keywords"] else None,
                result["fallback_reason"],
            ),
        )
        count += 1
        cat = result["category"]
        sev = result["severity"]
        kw = result["matched_keywords"]
        print(f"  [{cat}/{sev}] {inc['title'][:60]}  (keywords: {kw})")

    conn.commit()
    conn.close()
    print(f"\nSeeded {count} incidents into guardian.db")


if __name__ == "__main__":
    seed()
