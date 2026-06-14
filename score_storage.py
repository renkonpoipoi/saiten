from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from server import ROOT, SCORE_FIELDS, SCORES_PATH, missing_required_scores, normalize_score_entry


DB_PATH = Path(os.environ.get("SCORE_DB_PATH", ROOT / "data" / "scores.sqlite3"))
_LOCK = threading.Lock()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_storage() -> None:
    with _LOCK, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS score_entries (
              project_id TEXT NOT NULL,
              judge_id TEXT NOT NULL,
              team_id TEXT NOT NULL,
              entry_json TEXT NOT NULL,
              total INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (project_id, judge_id, team_id)
            );

            CREATE TABLE IF NOT EXISTS submissions (
              project_id TEXT NOT NULL,
              judge_id TEXT NOT NULL,
              submitted_at TEXT NOT NULL,
              PRIMARY KEY (project_id, judge_id)
            );

            CREATE TABLE IF NOT EXISTS judge_sessions (
              session_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              project_name TEXT NOT NULL,
              judge_id TEXT NOT NULL,
              judge_name TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        if _is_empty(conn):
            _migrate_json_scores(conn)


def _is_empty(conn: sqlite3.Connection) -> bool:
    score_count = conn.execute("SELECT COUNT(*) FROM score_entries").fetchone()[0]
    submission_count = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    return score_count == 0 and submission_count == 0


def _migrate_json_scores(conn: sqlite3.Connection) -> None:
    if not SCORES_PATH.exists():
        return
    try:
        store = json.loads(SCORES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    for project_id, project_scores in store.get("scores", {}).items():
        for judge_id, judge_scores in project_scores.items():
            for team_id, entry in judge_scores.items():
                if not isinstance(entry, dict):
                    continue
                updated_at = str(entry.get("updatedAt") or datetime.now(timezone.utc).isoformat())
                normalized = normalize_score_entry(entry)
                normalized["updatedAt"] = updated_at
                conn.execute(
                    """
                    INSERT OR REPLACE INTO score_entries
                    (project_id, judge_id, team_id, entry_json, total, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        judge_id,
                        team_id,
                        json.dumps(normalized, ensure_ascii=False),
                        normalized["total"],
                        updated_at,
                    ),
                )

    for project_id, project_submissions in store.get("submissions", {}).items():
        for judge_id, submission in project_submissions.items():
            submitted_at = (
                submission.get("submittedAt")
                if isinstance(submission, dict)
                else datetime.now(timezone.utc).isoformat()
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO submissions (project_id, judge_id, submitted_at)
                VALUES (?, ?, ?)
                """,
                (project_id, judge_id, submitted_at),
            )


def save_judge_session(session: dict) -> None:
    with _LOCK, connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO judge_sessions
            (session_id, project_id, project_name, judge_id, judge_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session["id"],
                session["projectId"],
                session["projectName"],
                session["judgeId"],
                session["judgeName"],
                session["createdAt"],
            ),
        )


def get_judge_scores(project_id: str, judge_id: str) -> dict:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT team_id, entry_json, updated_at
            FROM score_entries
            WHERE project_id = ? AND judge_id = ?
            """,
            (project_id, judge_id),
        ).fetchall()
    scores = {}
    for row in rows:
        entry = json.loads(row["entry_json"])
        entry.setdefault("updatedAt", row["updated_at"])
        scores[row["team_id"]] = entry
    return scores


def is_submitted(project_id: str, judge_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM submissions WHERE project_id = ? AND judge_id = ?",
            (project_id, judge_id),
        ).fetchone()
    return row is not None


def save_score(project_id: str, judge_id: str, team_id: str, entry: object) -> dict:
    normalized = normalize_score_entry(entry)
    updated_at = datetime.now(timezone.utc).isoformat()
    normalized["updatedAt"] = updated_at
    with _LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _is_submitted_conn(conn, project_id, judge_id):
            raise SubmittedError("Submitted scores cannot be changed.")
        conn.execute(
            """
            INSERT INTO score_entries
            (project_id, judge_id, team_id, entry_json, total, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, judge_id, team_id) DO UPDATE SET
              entry_json = excluded.entry_json,
              total = excluded.total,
              updated_at = excluded.updated_at
            """,
            (
                project_id,
                judge_id,
                team_id,
                json.dumps(normalized, ensure_ascii=False),
                normalized["total"],
                updated_at,
            ),
        )
    return normalized


def submit_scores(project: dict, project_id: str, judge_id: str) -> tuple[bool, list[dict], str | None]:
    submitted_at = datetime.now(timezone.utc).isoformat()
    with _LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        judge_scores = _get_judge_scores_conn(conn, project_id, judge_id)
        missing = missing_required_scores(project, judge_scores)
        if missing:
            return False, missing, None
        conn.execute(
            """
            INSERT INTO submissions (project_id, judge_id, submitted_at)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id, judge_id) DO UPDATE SET
              submitted_at = excluded.submitted_at
            """,
            (project_id, judge_id, submitted_at),
        )
    return True, [], submitted_at


def admin_summary_from_storage(project: dict) -> dict:
    with connect() as conn:
        all_scores = _get_project_scores_conn(conn, project["id"])
        submitted_rows = conn.execute(
            "SELECT judge_id, submitted_at FROM submissions WHERE project_id = ?",
            (project["id"],),
        ).fetchall()
    project_submissions = {row["judge_id"]: {"submittedAt": row["submitted_at"]} for row in submitted_rows}

    judges = []
    for judge in project.get("judges", []):
        judge_id = judge["id"]
        judge_scores = all_scores.get(judge_id, {})
        missing = missing_required_scores(project, judge_scores)
        submission = project_submissions.get(judge_id)
        judges.append(
            {
                "id": judge_id,
                "name": judge["name"],
                "submitted": bool(submission),
                "submittedAt": submission.get("submittedAt") if submission else None,
                "complete": not missing,
                "missingCount": len(missing),
            }
        )

    submitted_judge_ids = {judge["id"] for judge in judges if judge["submitted"]}
    team_results = []
    for team in project.get("teams", []):
        judge_totals = []
        for judge in project.get("judges", []):
            if judge["id"] not in submitted_judge_ids:
                continue
            entry = all_scores.get(judge["id"], {}).get(team["id"], {})
            total = entry.get("total", "")
            if isinstance(total, int):
                judge_totals.append({"judgeId": judge["id"], "judgeName": judge["name"], "total": total})
        team_total = sum(item["total"] for item in judge_totals)
        team_results.append(
            {
                "id": team["id"],
                "name": team["name"],
                "order": team.get("order", 0),
                "total": team_total,
                "average": round(team_total / len(judge_totals), 2) if judge_totals else 0,
                "judgeTotals": judge_totals,
            }
        )
    team_results.sort(key=lambda item: (-item["total"], item["order"]))

    submitted_count = len(submitted_judge_ids)
    total_judges = len(project.get("judges", []))
    return {
        "project": {
            "id": project["id"],
            "name": project["name"],
            "status": project.get("status", "open"),
            "teams": project.get("teams", []),
            "judges": project.get("judges", []),
        },
        "judges": judges,
        "submittedCount": submitted_count,
        "totalJudges": total_judges,
        "allSubmitted": total_judges > 0 and submitted_count == total_judges,
        "teamResults": team_results,
    }


def _is_submitted_conn(conn: sqlite3.Connection, project_id: str, judge_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM submissions WHERE project_id = ? AND judge_id = ?",
        (project_id, judge_id),
    ).fetchone()
    return row is not None


def _get_judge_scores_conn(conn: sqlite3.Connection, project_id: str, judge_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT team_id, entry_json, updated_at
        FROM score_entries
        WHERE project_id = ? AND judge_id = ?
        """,
        (project_id, judge_id),
    ).fetchall()
    scores = {}
    for row in rows:
        entry = json.loads(row["entry_json"])
        entry.setdefault("updatedAt", row["updated_at"])
        scores[row["team_id"]] = entry
    return scores


def _get_project_scores_conn(conn: sqlite3.Connection, project_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT judge_id, team_id, entry_json, updated_at
        FROM score_entries
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchall()
    scores = {}
    for row in rows:
        entry = json.loads(row["entry_json"])
        entry.setdefault("updatedAt", row["updated_at"])
        scores.setdefault(row["judge_id"], {})[row["team_id"]] = entry
    return scores


class SubmittedError(Exception):
    pass
