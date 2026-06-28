from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory

from server import (
    ROOT,
    find_judge,
    find_project,
    find_team,
    load_json,
    public_project,
)
from score_storage import (
    SubmittedError,
    admin_summary_from_storage,
    get_judge_scores,
    init_storage,
    is_submitted,
    save_judge_session,
    save_score,
    submit_scores as submit_scores_to_storage,
)


app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
init_storage()

JST = timezone(timedelta(hours=9))
ENTRY_WINDOW_START = datetime(2026, 7, 2, 14, 30, tzinfo=JST)
ENTRY_WINDOW_END = datetime(2026, 7, 2, 16, 10, tzinfo=JST)
ENTRY_WINDOW_LABEL = "2026年7月2日 14:30〜16:10"


@app.get("/")
def home():
    return redirect("/input")


@app.get("/input")
def input_page():
    return send_from_directory(ROOT, "scorer.html")


@app.get("/admin")
@app.get("/result")
def hidden_page():
    return redirect("/input")


@app.get("/api/projects")
def projects():
    return jsonify(load_json(ROOT / "data" / "scoring_projects.json"))


@app.get("/api/scores")
def get_scores():
    project_id = request.args.get("projectId", "").strip()
    judge_id = request.args.get("judgeId", "").strip()
    project = find_project(project_id)
    judge = find_judge(project, judge_id) if project else None
    if not project or not judge:
        return jsonify({"error": "Project or judge was not found."}), 400
    return jsonify(
        {
            "scores": get_judge_scores(project_id, judge_id),
            "submitted": is_submitted(project_id, judge_id),
            "entryWindow": entry_window_status(),
        }
    )


@app.get("/api/entry-window")
def entry_window():
    return jsonify(entry_window_status())


@app.get("/api/result/summary")
def result_summary_api():
    project = find_project(request.args.get("projectId", "").strip() or default_project_id())
    if not project:
        return jsonify({"error": "Project was not found."}), 400
    return jsonify(admin_summary_from_storage(project))


@app.post("/api/judge-session")
def judge_session():
    payload = request.get_json(silent=True) or {}
    project_id = str(payload.get("projectId", "")).strip()
    judge_id = str(payload.get("judgeId", "")).strip()
    project = find_project(project_id)
    judge = find_judge(project, judge_id) if project else None
    if not project or not judge:
        return jsonify({"error": "Project or judge was not found."}), 400
    judge_session_data = {
        "id": f"session-{secrets.token_urlsafe(12)}",
        "projectId": project["id"],
        "projectName": project["name"],
        "judgeId": judge["id"],
        "judgeName": judge["name"],
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    save_judge_session(judge_session_data)
    return jsonify(
        {
            "session": judge_session_data,
            "project": public_project(project),
            "entryWindow": entry_window_status(),
        }
    )


@app.post("/api/scores")
def save_scores():
    payload = request.get_json(silent=True) or {}
    project_id = str(payload.get("projectId", "")).strip()
    judge_id = str(payload.get("judgeId", "")).strip()
    team_id = str(payload.get("teamId", "")).strip()
    project = find_project(project_id)
    judge = find_judge(project, judge_id) if project else None
    team = find_team(project, team_id) if project else None
    if not project or not judge or not team:
        return jsonify({"error": "Project, judge, or team was not found."}), 400
    if not is_entry_window_open():
        return jsonify({"error": entry_window_closed_message(), "entryWindow": entry_window_status()}), 403

    try:
        entry = save_score(project_id, judge_id, team_id, payload.get("entry", {}))
    except SubmittedError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"entry": entry})


@app.post("/api/submit")
def submit_scores():
    payload = request.get_json(silent=True) or {}
    project_id = str(payload.get("projectId", "")).strip()
    judge_id = str(payload.get("judgeId", "")).strip()
    project = find_project(project_id)
    judge = find_judge(project, judge_id) if project else None
    if not project or not judge:
        return jsonify({"error": "Project or judge was not found."}), 400
    if not is_entry_window_open():
        return jsonify({"error": entry_window_closed_message(), "entryWindow": entry_window_status()}), 403

    ok, missing, submitted_at = submit_scores_to_storage(project, project_id, judge_id)
    if missing:
        return jsonify({"error": "All team scores are required before submission.", "missing": missing}), 400
    return jsonify({"submitted": ok, "submittedAt": submitted_at})


@app.get("/<path:filename>")
def static_files(filename: str):
    path = Path(filename)
    if path.parts and path.parts[0] == "assets":
        return send_from_directory(ROOT, filename)
    if filename in {"scorer.css", "scorer.js"}:
        return send_from_directory(ROOT, filename)
    return jsonify({"error": "Not found"}), 404


def default_project_id() -> str:
    projects = load_json(ROOT / "data" / "scoring_projects.json", {"projects": []}).get("projects", [])
    return str(projects[0].get("id", "")) if projects else ""


def now_jst() -> datetime:
    return datetime.now(JST)


def is_entry_window_open() -> bool:
    current = now_jst()
    return ENTRY_WINDOW_START <= current <= ENTRY_WINDOW_END


def entry_window_closed_message() -> str:
    if now_jst() < ENTRY_WINDOW_START:
        return f"入力開始前です。入力可能時間は {ENTRY_WINDOW_LABEL} です。"
    return f"入力時間は終了しました。入力可能時間は {ENTRY_WINDOW_LABEL} でした。"


def entry_window_status() -> dict:
    return {
        "open": is_entry_window_open(),
        "label": ENTRY_WINDOW_LABEL,
        "start": ENTRY_WINDOW_START.isoformat(),
        "end": ENTRY_WINDOW_END.isoformat(),
        "now": now_jst().isoformat(),
        "message": "入力受付中です。" if is_entry_window_open() else entry_window_closed_message(),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    app.run(host="0.0.0.0", port=port)
