"""Phase 6A: MVP hardening のテスト。

- Criterion 5件固定がserver-sideで保証されていること
- security headerが付与されていること
- Host Dashboard pollingが依存するAPI/DOM要素の整合
- Result Presentationの静的asset・DOM対応
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.models import Criterion, Project

BASE_PAYLOAD = {
    "name": "Phase6テスト",
    "subjects": ["チームA", "チームB"],
    "scorers": ["採点者1"],
    "criteria": ["軸1", "軸2", "軸3", "軸4", "軸5"],
    "allow_host_scoring": False,
}

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def _create_project(client, payload=None):
    resp = client.post("/api/projects", json=payload or BASE_PAYLOAD)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _login_host(client, host_code):
    assert client.post("/api/auth/host-login", json={"code": host_code}).status_code == 200


# ---------------------------------------------------------------------------
# Criterion 5件固定
# ---------------------------------------------------------------------------


def test_create_always_produces_exactly_five_criteria(client, db):
    data = _create_project(client)
    assert Criterion.query.filter_by(project_id=data["project_id"]).count() == 5


def test_create_rejects_fewer_than_five_criteria(client):
    resp = client.post("/api/projects", json=dict(BASE_PAYLOAD, criteria=["a", "b", "c", "d"]))
    assert resp.status_code == 400


def test_create_rejects_more_than_five_criteria(client):
    resp = client.post(
        "/api/projects", json=dict(BASE_PAYLOAD, criteria=["a", "b", "c", "d", "e", "f"])
    )
    assert resp.status_code == 400


def test_no_criterion_create_or_delete_api_exists(app):
    """MVPではCriterionの追加/削除APIを一切公開しない(5件固定の構造的保証)。

    名称編集(PATCH)のみが存在することを確認する。
    """
    criterion_rules = [
        rule for rule in app.url_map.iter_rules() if "/criteria" in rule.rule
    ]
    assert criterion_rules, "criterion route should exist for renaming"
    for rule in criterion_rules:
        methods = rule.methods - {"HEAD", "OPTIONS"}
        assert methods == {"PATCH"}, f"unexpected methods {methods} on {rule.rule}"


def test_criterion_rename_allowed_in_draft_only(client, db):
    data = _create_project(client)
    project_id = data["project_id"]
    _login_host(client, data["host_code"])
    criterion = Criterion.query.filter_by(project_id=project_id).first()

    resp = client.patch(
        f"/api/projects/{project_id}/criteria/{criterion.id}", json={"name": "改名後"}
    )
    assert resp.status_code == 200

    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})
    resp = client.patch(
        f"/api/projects/{project_id}/criteria/{criterion.id}", json={"name": "再改名"}
    )
    assert resp.status_code == 409

    # 件数は常に5件のまま
    assert Criterion.query.filter_by(project_id=project_id).count() == 5


def test_criterion_count_unchanged_through_full_lifecycle(client, app, db):
    data = _create_project(client)
    project_id = data["project_id"]
    _login_host(client, data["host_code"])
    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})

    scorer = app.test_client()
    scorer.post("/api/auth/scorer-login", json={"code": data["scorers"][0]["code"]})
    criteria = Criterion.query.filter_by(project_id=project_id).all()
    dashboard = scorer.get("/api/scorer/me/evaluations").get_json()
    for row in dashboard["subjects"]:
        scorer.post(
            f"/api/evaluations/{row['evaluation_id']}/scores",
            json={"scores": {str(c.id): 10 for c in criteria}},
        )
        scorer.post(f"/api/evaluations/{row['evaluation_id']}/submit")

    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"})
    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "PRESENTING"})

    summary = client.get(f"/api/projects/{project_id}/result-summary").get_json()
    assert len(summary["criteria"]) == 5
    assert Criterion.query.filter_by(project_id=project_id).count() == 5


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_security_headers_present_on_page_response(client):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]


def test_security_headers_present_on_api_response(client):
    resp = client.post("/api/auth/host-login", json={"code": "host_nope"})
    assert resp.status_code == 403
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------------------
# Host Dashboard polling(APIとDOMの整合)
# ---------------------------------------------------------------------------


def test_progress_api_returns_all_fields_polling_depends_on(client, db):
    data = _create_project(client)
    project_id = data["project_id"]
    _login_host(client, data["host_code"])
    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})

    body = client.get(f"/api/projects/{project_id}/progress").get_json()
    for key in (
        "project_status",
        "submitted_count",
        "total_count",
        "eligible_scorer_count",
        "incomplete_scorer_count",
        "subjects",
        "scorers",
    ):
        assert key in body, f"missing polling field: {key}"
    assert all("statuses" in s for s in body["scorers"])


def test_host_dashboard_template_has_polling_target_elements(client, db):
    data = _create_project(client)
    _login_host(client, data["host_code"])
    html = client.get(f"/host/{data['project_id']}").get_data(as_text=True)
    for element_id in (
        "projectStatusBadge",
        "progressSummary",
        "eligibleCount",
        "incompleteCount",
        "matrixHeaderRow",
        "matrixBody",
        "connectionWarning",
        "pollingIndicator",
        "draftNotice",
        "draftSettingsLink",
    ):
        assert f'id="{element_id}"' in html, f"missing element: {element_id}"


def test_host_dashboard_js_declares_polling_safeguards():
    """pollingの安全策(可視性判定・重複防止・停止条件)が実装されていること。"""
    js = (APP_DIR / "static" / "js" / "host_dashboard.js").read_text(encoding="utf-8")
    assert "document.hidden" in js
    assert "inFlight" in js
    assert "visibilitychange" in js
    assert "stopPolling" in js


# ---------------------------------------------------------------------------
# Result Presentation の品質確認
# ---------------------------------------------------------------------------


def test_presentation_static_assets_served(client):
    for path in (
        "/static/css/common.css",
        "/static/css/reveal.css",
        "/static/js/common.js",
        "/static/js/result_presentation.js",
        "/static/assets/reveal-hit.m4a",
        "/static/assets/reveal-sting.m4a",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def test_presentation_page_references_only_existing_assets(client, db):
    data = _create_project(client)
    _login_host(client, data["host_code"])
    html = client.get(f"/host/{data['project_id']}/present").get_data(as_text=True)

    for href in re.findall(r'(?:href|src)="(/static/[^"]+)"', html):
        assert client.get(href).status_code == 200, f"404 asset: {href}"


def test_presentation_dom_ids_match_js_expectations(client, db):
    data = _create_project(client)
    _login_host(client, data["host_code"])
    html = client.get(f"/host/{data['project_id']}/present").get_data(as_text=True)
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")

    for element_id in re.findall(r'getElementById\("([^"]+)"\)', js):
        assert f'id="{element_id}"' in html, f"JS references missing DOM id: {element_id}"


def test_result_presentation_js_audio_paths_exist(client):
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")
    for audio_path in re.findall(r'new Audio\("([^"]+)"\)', js):
        assert client.get(audio_path).status_code == 200, f"missing audio: {audio_path}"


def test_all_js_files_pass_syntax_check():
    js_dir = APP_DIR / "static" / "js"
    for js_file in sorted(js_dir.glob("*.js")):
        result = subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True)
        assert result.returncode == 0, f"{js_file.name}: {result.stderr}"


# ---------------------------------------------------------------------------
# Host Login の遷移先
# ---------------------------------------------------------------------------


def test_host_login_js_redirects_to_dashboard():
    js = (APP_DIR / "static" / "js" / "host_login.js").read_text(encoding="utf-8")
    assert "/host/${result.project_id}`" in js
    assert "/settings" not in js, "host login should land on the dashboard, not settings"
