from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from server import (
    ROOT,
    SCORES_PATH,
    get_score_fields,
    missing_fields_for_entry,
    missing_required_scores,
    normalize_score_entry,
)


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
        _ensure_finalized_at_column(conn)
        if _is_empty(conn):
            _migrate_json_scores(conn)
        _backfill_finalized_at_from_submissions(conn)


def _ensure_finalized_at_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(score_entries)")}
    if "finalized_at" not in columns:
        conn.execute("ALTER TABLE score_entries ADD COLUMN finalized_at TEXT")


def _backfill_finalized_at_from_submissions(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE score_entries
        SET finalized_at = (
            SELECT submitted_at FROM submissions s
            WHERE s.project_id = score_entries.project_id
              AND s.judge_id = score_entries.judge_id
        )
        WHERE finalized_at IS NULL
          AND EXISTS (
            SELECT 1 FROM submissions s
            WHERE s.project_id = score_entries.project_id
              AND s.judge_id = score_entries.judge_id
          )
        """
    )


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
            SELECT team_id, entry_json, updated_at, finalized_at
            FROM score_entries
            WHERE project_id = ? AND judge_id = ?
            """,
            (project_id, judge_id),
        ).fetchall()
    scores = {}
    for row in rows:
        entry = json.loads(row["entry_json"])
        entry.setdefault("updatedAt", row["updated_at"])
        entry["finalizedAt"] = row["finalized_at"]
        scores[row["team_id"]] = entry
    return scores


def is_submitted(project_id: str, judge_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM submissions WHERE project_id = ? AND judge_id = ?",
            (project_id, judge_id),
        ).fetchone()
    return row is not None


def save_score(project: dict, judge_id: str, team_id: str, entry: object) -> dict:
    project_id = project["id"]
    normalized = normalize_score_entry(entry, get_score_fields(project))
    updated_at = datetime.now(timezone.utc).isoformat()
    normalized["updatedAt"] = updated_at
    with _LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _is_team_finalized_conn(conn, project_id, judge_id, team_id):
            raise SubmittedError("Finalized scores cannot be changed.")
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
    normalized["finalizedAt"] = None
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


def finalize_team_score(project: dict, judge_id: str, team_id: str) -> dict:
    project_id = project["id"]
    finalized_at = datetime.now(timezone.utc).isoformat()
    with _LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT entry_json, finalized_at
            FROM score_entries
            WHERE project_id = ? AND judge_id = ? AND team_id = ?
            """,
            (project_id, judge_id, team_id),
        ).fetchone()
        if row is None:
            raise EntryNotFoundError("No saved score exists yet for this team.")
        if row["finalized_at"] is not None:
            raise AlreadyFinalizedError("This team's score is already finalized.")

        entry = json.loads(row["entry_json"])
        missing = missing_fields_for_entry(get_score_fields(project), entry)
        if missing:
            raise IncompleteEntryError(missing)

        conn.execute(
            """
            UPDATE score_entries SET finalized_at = ?
            WHERE project_id = ? AND judge_id = ? AND team_id = ?
            """,
            (finalized_at, project_id, judge_id, team_id),
        )
        entry["finalizedAt"] = finalized_at
    return entry


def admin_summary_from_storage(project: dict) -> dict:
    with connect() as conn:
        all_scores = _get_project_scores_conn(conn, project["id"])
        submitted_rows = conn.execute(
            "SELECT judge_id, submitted_at FROM submissions WHERE project_id = ?",
            (project["id"],),
        ).fetchall()
    project_submissions = {row["judge_id"]: {"submittedAt": row["submitted_at"]} for row in submitted_rows}

    team_ids = [team["id"] for team in project.get("teams", [])]
    total_teams = len(team_ids)
    total_judges = len(project.get("judges", []))

    judges = []
    for judge in project.get("judges", []):
        judge_id = judge["id"]
        judge_scores = all_scores.get(judge_id, {})
        missing = missing_required_scores(project, judge_scores)
        legacy_submission = project_submissions.get(judge_id)

        finalized_at_values = [judge_scores.get(team_id, {}).get("finalizedAt") for team_id in team_ids]
        finalized_team_count = sum(1 for value in finalized_at_values if value)
        all_teams_finalized = total_teams > 0 and finalized_team_count == total_teams

        if legacy_submission:
            submitted_at = legacy_submission["submittedAt"]
        elif all_teams_finalized:
            submitted_at = max(
                (value for value in finalized_at_values if value),
                key=lambda value: datetime.fromisoformat(value),
            )
        else:
            submitted_at = None

        judges.append(
            {
                "id": judge_id,
                "name": judge["name"],
                "submitted": bool(legacy_submission) or all_teams_finalized,
                "submittedAt": submitted_at,
                "complete": not missing,
                "missingCount": len(missing),
                "finalizedTeamCount": finalized_team_count,
                "totalTeamCount": total_teams,
                "allTeamsFinalized": all_teams_finalized,
            }
        )

    submitted_judge_ids = {judge["id"] for judge in judges if judge["submitted"]}
    team_results = []
    for team in project.get("teams", []):
        judge_totals = []
        finalized_judge_count = 0
        for judge in project.get("judges", []):
            entry = all_scores.get(judge["id"], {}).get(team["id"], {})
            if not entry.get("finalizedAt"):
                continue
            finalized_judge_count += 1
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
                "finalizedJudgeCount": finalized_judge_count,
                "totalJudgeCount": total_judges,
                "allJudgesFinalized": total_judges > 0 and finalized_judge_count == total_judges,
            }
        )
    team_results.sort(key=lambda item: (-item["total"], item["order"]))

    submitted_count = len(submitted_judge_ids)
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


def _is_team_finalized_conn(conn: sqlite3.Connection, project_id: str, judge_id: str, team_id: str) -> bool:
    row = conn.execute(
        "SELECT finalized_at FROM score_entries WHERE project_id = ? AND judge_id = ? AND team_id = ?",
        (project_id, judge_id, team_id),
    ).fetchone()
    return row is not None and row["finalized_at"] is not None


def _get_judge_scores_conn(conn: sqlite3.Connection, project_id: str, judge_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT team_id, entry_json, updated_at, finalized_at
        FROM score_entries
        WHERE project_id = ? AND judge_id = ?
        """,
        (project_id, judge_id),
    ).fetchall()
    scores = {}
    for row in rows:
        entry = json.loads(row["entry_json"])
        entry.setdefault("updatedAt", row["updated_at"])
        entry["finalizedAt"] = row["finalized_at"]
        scores[row["team_id"]] = entry
    return scores


def _get_project_scores_conn(conn: sqlite3.Connection, project_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT judge_id, team_id, entry_json, updated_at, finalized_at
        FROM score_entries
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchall()
    scores = {}
    for row in rows:
        entry = json.loads(row["entry_json"])
        entry.setdefault("updatedAt", row["updated_at"])
        entry["finalizedAt"] = row["finalized_at"]
        scores.setdefault(row["judge_id"], {})[row["team_id"]] = entry
    return scores


class SubmittedError(Exception):
    pass


class AlreadyFinalizedError(SubmittedError):
    pass


class EntryNotFoundError(Exception):
    pass


class IncompleteEntryError(Exception):
    def __init__(self, missing: list[str]):
        super().__init__("Entry is missing required fields.")
        self.missing = missing
