"""Phase 3: 採点(autosave/feedback/submit)のテスト。"""

from __future__ import annotations

from app.models import Criterion, Evaluation

VALID_PAYLOAD = {
    "name": "採点フローテスト",
    "subjects": ["チームA", "チームB"],
    "scorers": ["採点者1", "採点者2"],
    "criteria": ["独創性", "実用性", "デザイン", "技術力", "拡張性"],
    "allow_host_scoring": False,
}


def _create_and_start_scoring(client, payload=None):
    resp = client.post("/api/projects", json=payload or VALID_PAYLOAD)
    assert resp.status_code == 201
    data = resp.get_json()
    project_id = data["project_id"]

    login_resp = client.post("/api/auth/host-login", json={"code": data["host_code"]})
    assert login_resp.status_code == 200

    start_resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"}
    )
    assert start_resp.status_code == 200

    return data


def _login_scorer(scorer_client, code):
    resp = scorer_client.post("/api/auth/scorer-login", json={"code": code})
    assert resp.status_code == 200
    return resp.get_json()


def _evaluation_id_for_subject(scorer_client, subject_id):
    resp = scorer_client.get("/api/scorer/me/evaluations")
    assert resp.status_code == 200
    for row in resp.get_json()["subjects"]:
        if row["subject_id"] == subject_id:
            return row["evaluation_id"]
    raise AssertionError(f"subject {subject_id} not found in scorer dashboard")


def _full_score_payload(project_id):
    criteria = Criterion.query.filter_by(project_id=project_id).order_by(Criterion.sort_order).all()
    return {str(c.id): 15 for c in criteria}


# ---------------------------------------------------------------------------
# Scorer Dashboard / 本人限定アクセス
# ---------------------------------------------------------------------------


def test_scorer_sees_only_own_evaluations(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])

    resp = scorer_client.get("/api/scorer/me/evaluations")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_count"] == 2  # 2 subjects分だけ、他Scorerの分は含まれない
    assert all(row["state"] == "not_started" for row in body["subjects"])


def test_evaluation_access_without_scorer_session_forbidden(client):
    resp = client.get("/api/scorer/me/evaluations")
    assert resp.status_code == 403


def test_other_scorer_cannot_access_evaluation(client, app, db):
    data = _create_and_start_scoring(client)

    scorer1 = app.test_client()
    scorer2 = app.test_client()
    _login_scorer(scorer1, data["scorers"][0]["code"])
    _login_scorer(scorer2, data["scorers"][1]["code"])

    dashboard1 = scorer1.get("/api/scorer/me/evaluations").get_json()
    own_evaluation_id = dashboard1["subjects"][0]["evaluation_id"]

    # scorer2のセッションでscorer1のevaluationを取得しようとすると403
    resp = scorer2.get(f"/api/evaluations/{own_evaluation_id}")
    assert resp.status_code == 403

    resp = scorer2.post(f"/api/evaluations/{own_evaluation_id}/scores", json={"scores": {}})
    assert resp.status_code == 403

    resp = scorer2.post(f"/api/evaluations/{own_evaluation_id}/submit")
    assert resp.status_code == 403


def test_cross_project_scorer_cannot_access_other_project_evaluation(client, app, db):
    data_a = _create_and_start_scoring(client, dict(VALID_PAYLOAD, name="プロジェクトA"))

    client_b = app.test_client()
    data_b = _create_and_start_scoring(client_b, dict(VALID_PAYLOAD, name="プロジェクトB"))

    scorer_a = app.test_client()
    _login_scorer(scorer_a, data_a["scorers"][0]["code"])
    dashboard_a = scorer_a.get("/api/scorer/me/evaluations").get_json()
    evaluation_id_a = dashboard_a["subjects"][0]["evaluation_id"]

    scorer_b = app.test_client()
    _login_scorer(scorer_b, data_b["scorers"][0]["code"])

    resp = scorer_b.get(f"/api/evaluations/{evaluation_id_a}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# スコア検証
# ---------------------------------------------------------------------------


def test_score_below_zero_rejected(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])
    subject_id = None
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    evaluation_id = dashboard["subjects"][0]["evaluation_id"]
    criterion = Criterion.query.filter_by(project_id=data["project_id"]).first()

    resp = scorer_client.post(
        f"/api/evaluations/{evaluation_id}/scores",
        json={"scores": {str(criterion.id): -1}},
    )
    assert resp.status_code == 400


def test_score_above_max_rejected(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    evaluation_id = dashboard["subjects"][0]["evaluation_id"]
    criterion = Criterion.query.filter_by(project_id=data["project_id"]).first()

    resp = scorer_client.post(
        f"/api/evaluations/{evaluation_id}/scores",
        json={"scores": {str(criterion.id): criterion.max_score + 1}},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# autosave / feedback / submit
# ---------------------------------------------------------------------------


def test_autosave_persists_partial_scores_and_feedback(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    evaluation_id = dashboard["subjects"][0]["evaluation_id"]
    criterion = Criterion.query.filter_by(project_id=data["project_id"]).order_by(Criterion.sort_order).first()

    resp = scorer_client.post(
        f"/api/evaluations/{evaluation_id}/scores",
        json={"scores": {str(criterion.id): 12}, "feedback": "いい発表でした"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "draft"
    assert body["feedback"] == "いい発表でした"
    saved = {c["id"]: c["score"] for c in body["criteria"]}
    assert saved[criterion.id] == 12

    # ダッシュボード状態がin_progressになっていること
    dashboard2 = scorer_client.get("/api/scorer/me/evaluations").get_json()
    row = next(r for r in dashboard2["subjects"] if r["evaluation_id"] == evaluation_id)
    assert row["state"] == "in_progress"


def test_submit_rejected_when_scores_incomplete(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    evaluation_id = dashboard["subjects"][0]["evaluation_id"]

    resp = scorer_client.post(f"/api/evaluations/{evaluation_id}/submit")
    assert resp.status_code == 400


def test_submit_succeeds_when_all_scores_present(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    evaluation_id = dashboard["subjects"][0]["evaluation_id"]

    scores = _full_score_payload(data["project_id"])
    resp = scorer_client.post(
        f"/api/evaluations/{evaluation_id}/scores", json={"scores": scores}
    )
    assert resp.status_code == 200

    resp = scorer_client.post(f"/api/evaluations/{evaluation_id}/submit")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "submitted"

    evaluation = db.session.get(Evaluation, evaluation_id)
    assert evaluation.status == "submitted"
    assert evaluation.submitted_at is not None


def test_update_rejected_after_submission(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    evaluation_id = dashboard["subjects"][0]["evaluation_id"]

    scores = _full_score_payload(data["project_id"])
    scorer_client.post(f"/api/evaluations/{evaluation_id}/scores", json={"scores": scores})
    scorer_client.post(f"/api/evaluations/{evaluation_id}/submit")

    resp = scorer_client.post(
        f"/api/evaluations/{evaluation_id}/scores", json={"scores": scores, "feedback": "変更"}
    )
    assert resp.status_code == 409


def test_double_submit_is_idempotent(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    evaluation_id = dashboard["subjects"][0]["evaluation_id"]

    scores = _full_score_payload(data["project_id"])
    scorer_client.post(f"/api/evaluations/{evaluation_id}/scores", json={"scores": scores})
    first = scorer_client.post(f"/api/evaluations/{evaluation_id}/submit")
    assert first.status_code == 200

    second = scorer_client.post(f"/api/evaluations/{evaluation_id}/submit")
    assert second.status_code == 200
    assert second.get_json()["status"] == "submitted"


def test_scorer_dashboard_progress_updates_after_submit(client, app, db):
    data = _create_and_start_scoring(client)
    scorer_client = app.test_client()
    _login_scorer(scorer_client, data["scorers"][0]["code"])
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    assert dashboard["submitted_count"] == 0

    for row in dashboard["subjects"]:
        scores = _full_score_payload(data["project_id"])
        scorer_client.post(f"/api/evaluations/{row['evaluation_id']}/scores", json={"scores": scores})
        scorer_client.post(f"/api/evaluations/{row['evaluation_id']}/submit")

    updated = scorer_client.get("/api/scorer/me/evaluations").get_json()
    assert updated["submitted_count"] == updated["total_count"] == 2
    assert all(row["state"] == "submitted" for row in updated["subjects"])
