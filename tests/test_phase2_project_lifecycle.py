"""Phase 2: プロジェクト作成・コード認証・DRAFT編集・DRAFT->SCORING遷移のテスト。"""

from __future__ import annotations

import re

import pytest

from app.models import Criterion, Evaluation, Project, Scorer, Subject
from app.services.code_service import hash_code

VALID_PAYLOAD = {
    "name": "テストプロジェクト",
    "subjects": ["チームA", "チームB"],
    "scorers": ["採点者1", "採点者2"],
    "criteria": ["独創性", "実用性", "デザイン", "技術力", "拡張性"],
    "allow_host_scoring": False,
}


def _create_project(client, payload=None):
    resp = client.post("/api/projects", json=payload or VALID_PAYLOAD)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _extract_csrf_token(client, page_url="/projects/new"):
    resp = client.get(page_url)
    html = resp.get_data(as_text=True)
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', html)
    assert match, "csrf-token meta tag not found"
    return match.group(1)


# ---------------------------------------------------------------------------
# プロジェクト作成
# ---------------------------------------------------------------------------


def test_create_project_creates_five_criteria_and_hashes_codes(client, db):
    data = _create_project(client)
    project_id = data["project_id"]

    criteria = Criterion.query.filter_by(project_id=project_id).all()
    assert len(criteria) == 5
    assert all(c.max_score == 20 for c in criteria)

    scorers = Scorer.query.filter_by(project_id=project_id).all()
    assert len(scorers) == 2

    project = db.session.get(Project, project_id)
    assert project.status == "DRAFT"
    # DBには平文が一切残っていないこと
    assert project.host_code_hash == hash_code(data["host_code"])
    assert data["host_code"] not in project.host_code_hash
    for scorer, scorer_row in zip(scorers, data["scorers"]):
        assert scorer.access_code_hash == hash_code(scorer_row["code"])


def test_create_project_requires_exactly_five_criteria(client):
    payload = dict(VALID_PAYLOAD, criteria=["a", "b", "c"])
    resp = client.post("/api/projects", json=payload)
    assert resp.status_code == 400
    assert "5" in resp.get_json()["error"] or "criteria" in resp.get_json()["error"].lower()


def test_create_project_with_allow_host_scoring_adds_host_scorer(client, db):
    payload = dict(VALID_PAYLOAD, allow_host_scoring=True)
    data = _create_project(client, payload)
    scorers = Scorer.query.filter_by(project_id=data["project_id"]).all()
    assert len(scorers) == 3
    assert sum(1 for s in scorers if s.is_host_scorer) == 1


def test_create_project_requires_name(client):
    payload = dict(VALID_PAYLOAD, name="  ")
    resp = client.post("/api/projects", json=payload)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------


def test_host_login_success_sets_session(client):
    data = _create_project(client)
    resp = client.post("/api/auth/host-login", json={"code": data["host_code"]})
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess["host_project_id"] == data["project_id"]


def test_host_login_invalid_code_rejected(client):
    _create_project(client)
    resp = client.post("/api/auth/host-login", json={"code": "host_wrong-code"})
    assert resp.status_code == 403
    with client.session_transaction() as sess:
        assert "host_project_id" not in sess


def test_scorer_login_success_sets_session(client):
    data = _create_project(client)
    scorer_code = data["scorers"][0]["code"]
    resp = client.post("/api/auth/scorer-login", json={"code": scorer_code})
    assert resp.status_code == 200
    body = resp.get_json()
    with client.session_transaction() as sess:
        assert sess["scorer_id"] == body["scorer_id"]
        assert sess["scorer_project_id"] == data["project_id"]


def test_scorer_login_invalid_code_rejected(client):
    _create_project(client)
    resp = client.post("/api/auth/scorer-login", json={"code": "scr_wrong-code"})
    assert resp.status_code == 403


def test_login_rate_limit_blocks_after_threshold(client_with_ratelimit_only):
    statuses = [
        client_with_ratelimit_only.post("/api/auth/host-login", json={"code": "host_nope"}).status_code
        for _ in range(15)
    ]
    assert 403 in statuses
    assert 429 in statuses
    # rate limit到達後は429が最後まで続くこと
    assert statuses[-1] == 429


def test_csrf_protection_rejects_missing_token(client_with_security):
    resp = client_with_security.post("/api/projects", json=VALID_PAYLOAD)
    assert resp.status_code == 400
    assert "CSRF" in resp.get_json()["error"]


def test_csrf_protection_accepts_valid_token(client_with_security):
    token = _extract_csrf_token(client_with_security)
    resp = client_with_security.post(
        "/api/projects", json=VALID_PAYLOAD, headers={"X-CSRFToken": token}
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# DRAFT編集 / cross-project authorization
# ---------------------------------------------------------------------------


def _login_as_host(client, host_code):
    resp = client.post("/api/auth/host-login", json={"code": host_code})
    assert resp.status_code == 200


def test_draft_edit_allowed_while_draft(client, db):
    data = _create_project(client)
    project_id = data["project_id"]
    _login_as_host(client, data["host_code"])

    resp = client.patch(f"/api/projects/{project_id}", json={"name": "改名後"})
    assert resp.status_code == 200
    assert db.session.get(Project, project_id).name == "改名後"

    resp = client.post(f"/api/projects/{project_id}/subjects", json={"name": "チームC"})
    assert resp.status_code == 201
    assert Subject.query.filter_by(project_id=project_id).count() == 3


def test_construction_edit_forbidden_after_scoring_started(client, db):
    data = _create_project(client)
    project_id = data["project_id"]
    _login_as_host(client, data["host_code"])

    resp = client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})
    assert resp.status_code == 200

    resp = client.patch(f"/api/projects/{project_id}", json={"name": "禁止されるはず"})
    assert resp.status_code == 409

    resp = client.post(f"/api/projects/{project_id}/subjects", json={"name": "追加禁止"})
    assert resp.status_code == 409

    resp = client.patch(
        f"/api/projects/{project_id}/criteria/{Criterion.query.filter_by(project_id=project_id).first().id}",
        json={"name": "改名禁止"},
    )
    assert resp.status_code == 409


def test_cross_project_authorization_is_rejected(client):
    project_a = _create_project(client, dict(VALID_PAYLOAD, name="プロジェクトA"))
    project_b = _create_project(client, dict(VALID_PAYLOAD, name="プロジェクトB"))

    _login_as_host(client, project_a["host_code"])

    # Aのホストとしてログイン中にBを操作しようとすると拒否される
    resp = client.patch(f"/api/projects/{project_b['project_id']}", json={"name": "乗っ取り"})
    assert resp.status_code == 403

    resp = client.get(f"/api/projects/{project_b['project_id']}")
    assert resp.status_code == 403

    # 自分のプロジェクトへの操作は成功する
    resp = client.get(f"/api/projects/{project_a['project_id']}")
    assert resp.status_code == 200


def test_scorer_code_regeneration_allowed_after_scoring_started(client, db):
    """コード再発行はDRAFT限定ではなく常時可能(実装計画 v2 11節)。"""
    data = _create_project(client)
    project_id = data["project_id"]
    scorer_id = data["scorers"][0]["id"]
    _login_as_host(client, data["host_code"])

    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})

    resp = client.post(f"/api/projects/{project_id}/scorers/{scorer_id}/regenerate-code")
    assert resp.status_code == 200
    new_code = resp.get_json()["code"]
    scorer = db.session.get(Scorer, scorer_id)
    assert scorer.access_code_hash == hash_code(new_code)


# ---------------------------------------------------------------------------
# DRAFT -> SCORING
# ---------------------------------------------------------------------------


def test_transition_to_scoring_creates_evaluations_for_all_pairs(client, db):
    data = _create_project(client)
    project_id = data["project_id"]
    _login_as_host(client, data["host_code"])

    resp = client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})
    assert resp.status_code == 200
    assert db.session.get(Project, project_id).status == "SCORING"

    evaluations = Evaluation.query.filter_by(project_id=project_id).all()
    # 2 subjects x 2 scorers = 4
    assert len(evaluations) == 4
    assert all(e.status == "draft" for e in evaluations)
    pairs = {(e.scorer_id, e.subject_id) for e in evaluations}
    assert len(pairs) == 4


def test_double_transition_does_not_duplicate_evaluations(client, db):
    data = _create_project(client)
    project_id = data["project_id"]
    _login_as_host(client, data["host_code"])

    resp1 = client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})
    assert resp1.status_code == 200

    resp2 = client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})
    assert resp2.status_code == 409

    assert Evaluation.query.filter_by(project_id=project_id).count() == 4


def test_transition_requires_active_scorer_and_subject(client):
    payload = dict(VALID_PAYLOAD, subjects=["唯一のチーム"], scorers=["唯一の採点者"])
    data = _create_project(client, payload)
    project_id = data["project_id"]
    _login_as_host(client, data["host_code"])

    # 全被採点者を削除するとscoring開始を拒否される
    subject_id = Subject.query.filter_by(project_id=project_id).first().id
    resp = client.delete(f"/api/projects/{project_id}/subjects/{subject_id}")
    assert resp.status_code == 204

    resp = client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})
    assert resp.status_code == 409
