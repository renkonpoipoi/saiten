from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PROJECTS_PATH = DATA_DIR / "scoring_projects.json"
SESSIONS_PATH = DATA_DIR / "judge_sessions.json"
SCORES_PATH = DATA_DIR / "scores.json"
ADMIN_USERS_PATH = DATA_DIR / "admin_users.json"
ADMIN_SESSIONS_PATH = DATA_DIR / "admin_sessions.json"

SCORE_FIELDS = {
    "originality": "独創性",
    "usefulness": "実用性",
    "design": "UI/UXデザイン",
    "technical": "技術力",
    "scalability": "拡張性",
}
MAX_SCORE = 20


class ScoreAppHandler(SimpleHTTPRequestHandler):
    server_version = "M1ScoreInput/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.redirect("/input")
            return
        if path == "/input":
            self.serve_file(ROOT / "scorer.html", "text/html; charset=utf-8")
            return
        if path == "/admin":
            query = parse_qs(urlparse(self.path).query)
            admin_key = first_query_value(query, "key")
            if admin_key:
                admin = find_admin_by_code(admin_key)
                if not admin:
                    self.serve_file(ROOT / "admin_login.html", "text/html; charset=utf-8")
                    return
                self.create_admin_session(admin, redirect_to="/admin")
                return
            if not self.current_admin():
                self.serve_file(ROOT / "admin_login.html", "text/html; charset=utf-8")
                return
            self.serve_file(ROOT / "admin.html", "text/html; charset=utf-8")
            return
        if path == "/api/projects":
            self.send_json(load_json(PROJECTS_PATH))
            return
        if path == "/api/scores":
            self.get_scores()
            return
        if path == "/api/admin/summary":
            if not self.require_admin():
                return
            self.get_admin_summary()
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/judge-session":
            self.create_judge_session()
            return
        if path == "/api/scores":
            self.save_scores()
            return
        if path == "/api/submit":
            self.submit_scores()
            return
        if path == "/api/admin/login":
            self.admin_login()
            return
        if path == "/api/admin/logout":
            self.admin_logout()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def require_admin(self) -> bool:
        if self.current_admin():
            return True
        self.send_json({"error": "Admin login is required."}, HTTPStatus.UNAUTHORIZED)
        return False

    def current_admin(self) -> dict | None:
        token = cookie_value(self.headers.get("Cookie", ""), "admin_session")
        if not token:
            return None
        sessions = load_json(ADMIN_SESSIONS_PATH, {"sessions": {}}).get("sessions", {})
        session = sessions.get(token)
        if not session:
            return None
        return find_admin(session.get("adminId", ""))

    def admin_login(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        admin = find_admin_by_code(str(payload.get("accessCode", "")).strip())
        if not admin:
            self.send_json({"error": "アクセスコードが違います。"}, HTTPStatus.UNAUTHORIZED)
            return
        self.create_admin_session(admin)

    def create_admin_session(self, admin: dict, redirect_to: str | None = None) -> None:
        token = secrets.token_urlsafe(32)
        sessions = load_json(ADMIN_SESSIONS_PATH, {"sessions": {}})
        sessions.setdefault("sessions", {})[token] = {
            "adminId": admin["id"],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        save_json(ADMIN_SESSIONS_PATH, sessions)
        cookie = f"admin_session={token}; HttpOnly; SameSite=Lax; Path=/"
        if redirect_to:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", redirect_to)
            self.send_header("Set-Cookie", cookie)
            self.end_headers()
            return
        self.send_json_with_headers({"admin": {"id": admin["id"], "name": admin["name"]}}, {"Set-Cookie": cookie})

    def admin_logout(self) -> None:
        token = cookie_value(self.headers.get("Cookie", ""), "admin_session")
        if token:
            sessions = load_json(ADMIN_SESSIONS_PATH, {"sessions": {}})
            sessions.get("sessions", {}).pop(token, None)
            save_json(ADMIN_SESSIONS_PATH, sessions)
        self.send_json_with_headers(
            {"ok": True},
            {"Set-Cookie": "admin_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"},
        )

    def get_scores(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        project_id = first_query_value(query, "projectId")
        judge_id = first_query_value(query, "judgeId")
        if not find_project(project_id):
            self.send_json({"error": "Project was not found."}, HTTPStatus.BAD_REQUEST)
            return
        scores = load_json(SCORES_PATH, {"scores": {}, "submissions": {}}).get("scores", {})
        submitted = is_submitted(project_id, judge_id)
        judge_scores = scores.get(project_id, {}).get(judge_id, {})
        self.send_json({"scores": judge_scores, "submitted": submitted})

    def get_admin_summary(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        project_id = first_query_value(query, "projectId") or default_project_id()
        project = find_project(project_id)
        if not project:
            self.send_json({"error": "Project was not found."}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json(admin_summary(project))

    def create_judge_session(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        project_id = str(payload.get("projectId", "")).strip()
        judge_id = str(payload.get("judgeId", "")).strip()
        project = find_project(project_id)
        judge = find_judge(project, judge_id) if project else None
        if not project or not judge:
            self.send_json({"error": "Project or judge was not found."}, HTTPStatus.BAD_REQUEST)
            return

        session = {
            "id": f"session-{secrets.token_urlsafe(12)}",
            "projectId": project["id"],
            "projectName": project["name"],
            "judgeId": judge["id"],
            "judgeName": judge["name"],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        sessions = load_json(SESSIONS_PATH, {"sessions": []})
        sessions.setdefault("sessions", []).append(session)
        save_json(SESSIONS_PATH, sessions)
        self.send_json({"session": session, "project": public_project(project)})

    def save_scores(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        project_id = str(payload.get("projectId", "")).strip()
        judge_id = str(payload.get("judgeId", "")).strip()
        team_id = str(payload.get("teamId", "")).strip()
        project = find_project(project_id)
        judge = find_judge(project, judge_id) if project else None
        team = find_team(project, team_id) if project else None
        if not project or not judge or not team:
            self.send_json({"error": "Project, judge, or team was not found."}, HTTPStatus.BAD_REQUEST)
            return
        if is_submitted(project_id, judge_id):
            self.send_json({"error": "Submitted scores cannot be changed."}, HTTPStatus.CONFLICT)
            return

        entry = normalize_score_entry(payload.get("entry", {}))
        scores = load_json(SCORES_PATH, {"scores": {}, "submissions": {}})
        project_scores = scores.setdefault("scores", {}).setdefault(project_id, {})
        judge_scores = project_scores.setdefault(judge_id, {})
        judge_scores[team_id] = {
            **entry,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        save_json(SCORES_PATH, scores)
        self.send_json({"entry": judge_scores[team_id]})

    def submit_scores(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        project_id = str(payload.get("projectId", "")).strip()
        judge_id = str(payload.get("judgeId", "")).strip()
        project = find_project(project_id)
        judge = find_judge(project, judge_id) if project else None
        if not project or not judge:
            self.send_json({"error": "Project or judge was not found."}, HTTPStatus.BAD_REQUEST)
            return

        scores = load_json(SCORES_PATH, {"scores": {}, "submissions": {}})
        judge_scores = scores.setdefault("scores", {}).setdefault(project_id, {}).setdefault(judge_id, {})
        missing = missing_required_scores(project, judge_scores)
        if missing:
            self.send_json({"error": "All team scores are required before submission.", "missing": missing}, HTTPStatus.BAD_REQUEST)
            return

        submitted_at = datetime.now(timezone.utc).isoformat()
        scores.setdefault("submissions", {}).setdefault(project_id, {})[judge_id] = {
            "submittedAt": submitted_at,
        }
        save_json(SCORES_PATH, scores)
        self.send_json({"submitted": True, "submittedAt": submitted_at})

    def read_json_body(self) -> dict:
        length = int(self.headers.get("content-length", "0"))
        if length <= 0:
            raise ValueError("Request body is empty.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be JSON.") from exc

    def serve_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        data = path.read_bytes()
        guessed = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_json_with_headers(payload, {}, status)

    def send_json_with_headers(self, payload: dict, headers: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()


def load_json(path: Path, fallback: dict | None = None) -> dict:
    if not path.exists():
        return dict(fallback or {})
    return json.loads(path.read_text(encoding="utf-8"))


def score_store() -> dict:
    return load_json(SCORES_PATH, {"scores": {}, "submissions": {}})


def admin_users() -> list[dict]:
    return load_json(ADMIN_USERS_PATH, {"admins": []}).get("admins", [])


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_project(project_id: str) -> dict | None:
    projects = load_json(PROJECTS_PATH, {"projects": []}).get("projects", [])
    return next((project for project in projects if project.get("id") == project_id), None)


def default_project_id() -> str:
    projects = load_json(PROJECTS_PATH, {"projects": []}).get("projects", [])
    return str(projects[0].get("id", "")) if projects else ""


def find_admin(admin_id: str) -> dict | None:
    return next((admin for admin in admin_users() if admin.get("id") == admin_id), None)


def find_admin_by_code(access_code: str) -> dict | None:
    return next((admin for admin in admin_users() if admin.get("accessCode") == access_code), None)


def cookie_value(cookie_header: str, name: str) -> str:
    prefix = f"{name}="
    for part in cookie_header.split(";"):
        item = part.strip()
        if item.startswith(prefix):
            return item[len(prefix):]
    return ""


def find_judge(project: dict | None, judge_id: str) -> dict | None:
    if not project:
        return None
    return next((judge for judge in project.get("judges", []) if judge.get("id") == judge_id), None)


def find_team(project: dict | None, team_id: str) -> dict | None:
    if not project:
        return None
    return next((team for team in project.get("teams", []) if team.get("id") == team_id), None)


def first_query_value(query: dict, key: str) -> str:
    values = query.get(key) or [""]
    return str(values[0]).strip()


def normalize_score_entry(entry: object) -> dict:
    if not isinstance(entry, dict):
        entry = {}
    normalized = {}
    for key in SCORE_FIELDS:
        value = entry.get(key, "")
        normalized[key] = normalize_score_value(value)
    normalized["comment"] = str(entry.get("comment", "")).strip()
    normalized["total"] = sum(value for value in normalized.values() if isinstance(value, int))
    return normalized


def normalize_score_value(value: object) -> int | str:
    if value == "" or value is None:
        return ""
    try:
        score = int(value)
    except (TypeError, ValueError):
        return ""
    return max(0, min(MAX_SCORE, score))


def is_submitted(project_id: str, judge_id: str) -> bool:
    submissions = score_store().get("submissions", {})
    return bool(submissions.get(project_id, {}).get(judge_id))


def missing_required_scores(project: dict, judge_scores: dict) -> list[dict]:
    missing = []
    for team in project.get("teams", []):
        entry = judge_scores.get(team.get("id"), {})
        for field_key, field_label in SCORE_FIELDS.items():
            value = entry.get(field_key, "")
            if value == "" or value is None:
                missing.append({"teamId": team.get("id"), "teamName": team.get("name"), "field": field_label})
    return missing


def admin_summary(project: dict) -> dict:
    store = score_store()
    project_scores = store.get("scores", {}).get(project["id"], {})
    project_submissions = store.get("submissions", {}).get(project["id"], {})
    judges = []
    for judge in project.get("judges", []):
        judge_id = judge["id"]
        judge_scores = project_scores.get(judge_id, {})
        missing = missing_required_scores(project, judge_scores)
        submission = project_submissions.get(judge_id)
        judges.append(
            {
                "id": judge_id,
                "name": judge["name"],
                "submitted": bool(submission),
                "submittedAt": submission.get("submittedAt") if isinstance(submission, dict) else None,
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
            entry = project_scores.get(judge["id"], {}).get(team["id"], {})
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
        "project": public_project(project),
        "judges": judges,
        "submittedCount": submitted_count,
        "totalJudges": total_judges,
        "allSubmitted": total_judges > 0 and submitted_count == total_judges,
        "teamResults": team_results,
    }


def public_project(project: dict) -> dict:
    return {
        "id": project["id"],
        "name": project["name"],
        "status": project.get("status", "open"),
        "teams": project.get("teams", []),
        "judges": project.get("judges", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M1 score input app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ScoreAppHandler)
    print(f"Score input app running at http://{args.host}:{args.port}/input")
    server.serve_forever()


if __name__ == "__main__":
    main()
