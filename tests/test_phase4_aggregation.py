"""Phase 4: eligible scorer判定・SCORING->LOCKED遷移・集計/rankingのテスト。"""

from __future__ import annotations

from app.extensions import db
from app.models import Criterion, Evaluation, Project, Subject
from app.services import project_service, result_service

VALID_PAYLOAD = {
    "name": "集計テスト",
    "subjects": ["チームA", "チームB", "チームC"],
    "scorers": ["採点者1", "採点者2", "採点者3"],
    "criteria": ["独創性", "実用性", "デザイン", "技術力", "拡張性"],
    "allow_host_scoring": False,
}


def _create_and_start(client, payload=None):
    resp = client.post("/api/projects", json=payload or VALID_PAYLOAD)
    assert resp.status_code == 201
    data = resp.get_json()
    client.post("/api/auth/host-login", json={"code": data["host_code"]})
    resp = client.post(
        f"/api/projects/{data['project_id']}/transition", json={"target_status": "SCORING"}
    )
    assert resp.status_code == 200
    return data


def _login_scorer(scorer_client, code):
    resp = scorer_client.post("/api/auth/scorer-login", json={"code": code})
    assert resp.status_code == 200
    return resp.get_json()


def _submit_all(scorer_client, project_id, score_value=15):
    criteria = Criterion.query.filter_by(project_id=project_id).order_by(Criterion.sort_order).all()
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    for row in dashboard["subjects"]:
        scores = {str(c.id): score_value for c in criteria}
        scorer_client.post(
            f"/api/evaluations/{row['evaluation_id']}/scores", json={"scores": scores}
        )
        resp = scorer_client.post(f"/api/evaluations/{row['evaluation_id']}/submit")
        assert resp.status_code == 200


def _submit_partial(scorer_client, project_id, subject_ids_to_submit, score_value=10):
    criteria = Criterion.query.filter_by(project_id=project_id).order_by(Criterion.sort_order).all()
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    for row in dashboard["subjects"]:
        if row["subject_id"] not in subject_ids_to_submit:
            continue
        scores = {str(c.id): score_value for c in criteria}
        scorer_client.post(
            f"/api/evaluations/{row['evaluation_id']}/scores", json={"scores": scores}
        )
        resp = scorer_client.post(f"/api/evaluations/{row['evaluation_id']}/submit")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# eligible scorer判定
# ---------------------------------------------------------------------------


def test_eligible_scorer_requires_all_subjects_submitted(client, app, db):
    data = _create_and_start(client)
    project_id = data["project_id"]

    scorer1 = app.test_client()
    _login_scorer(scorer1, data["scorers"][0]["code"])
    _submit_all(scorer1, project_id)

    scorer2 = app.test_client()
    _login_scorer(scorer2, data["scorers"][1]["code"])
    subject_ids = [s.id for s in Subject.query.filter_by(project_id=project_id).all()]
    _submit_partial(scorer2, project_id, subject_ids[:1])  # 1件だけ提出、残りは未提出

    eligible = project_service.eligible_scorer_ids(project_id)
    scorer1_id = data["scorers"][0]["id"]
    scorer2_id = data["scorers"][1]["id"]
    assert scorer1_id in eligible
    assert scorer2_id not in eligible


def test_partial_scorer_fully_excluded_from_aggregation(client, app, db):
    """部分採点しか完了していないScorerは、他Subjectへのsubmitted評価も
    含めて公式集計から全て除外される。"""
    data = _create_and_start(client)
    project_id = data["project_id"]

    subjects = Subject.query.filter_by(project_id=project_id).order_by(Subject.sort_order).all()

    scorer1 = app.test_client()
    _login_scorer(scorer1, data["scorers"][0]["code"])
    _submit_all(scorer1, project_id, score_value=20)

    scorer2 = app.test_client()
    _login_scorer(scorer2, data["scorers"][1]["code"])
    # scorer2は最初のsubjectだけ提出、残りは未提出のまま(=非eligible)
    _submit_partial(scorer2, project_id, [subjects[0].id], score_value=20)

    project = db.session.get(Project, project_id)
    project_service.transition_to_locked(project)

    summary = result_service.build_result_summary(project)
    first_subject_result = next(r for r in summary["subjects"] if r["id"] == subjects[0].id)
    # scorer2(非eligible)の提出分が混入していれば scorer_count は2になってしまう
    assert first_subject_result["scorer_count"] == 1
    assert summary["eligible_scorer_count"] == 1


# ---------------------------------------------------------------------------
# SCORING -> LOCKED
# ---------------------------------------------------------------------------


def test_lock_rejected_when_no_eligible_scorer(client, db):
    data = _create_and_start(client)
    project_id = data["project_id"]
    # 誰も提出していない状態でロックしようとする
    resp = client.post(f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"})
    assert resp.status_code == 409


def test_forced_close_locks_with_partial_scorers(client, app, db):
    data = _create_and_start(client)
    project_id = data["project_id"]

    scorer1 = app.test_client()
    _login_scorer(scorer1, data["scorers"][0]["code"])
    _submit_all(scorer1, project_id)
    # scorer2, scorer3は何も提出しない(強制締切対象)

    resp = client.post(f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"})
    assert resp.status_code == 200
    assert db.session.get(Project, project_id).status == "LOCKED"


def test_scoring_write_rejected_after_locked(client, app, db):
    data = _create_and_start(client)
    project_id = data["project_id"]

    scorer1 = app.test_client()
    _login_scorer(scorer1, data["scorers"][0]["code"])
    _submit_all(scorer1, project_id)

    scorer2 = app.test_client()
    _login_scorer(scorer2, data["scorers"][1]["code"])
    dashboard = scorer2.get("/api/scorer/me/evaluations").get_json()
    evaluation_id = dashboard["subjects"][0]["evaluation_id"]  # scorer2はまだdraftのまま

    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"})

    resp = scorer2.post(
        f"/api/evaluations/{evaluation_id}/scores", json={"scores": {}}
    )
    assert resp.status_code == 409

    resp = scorer2.post(f"/api/evaluations/{evaluation_id}/submit")
    assert resp.status_code == 409


def test_progress_api_reports_eligible_and_incomplete_counts(client, app, db):
    data = _create_and_start(client)
    project_id = data["project_id"]

    scorer1 = app.test_client()
    _login_scorer(scorer1, data["scorers"][0]["code"])
    _submit_all(scorer1, project_id)

    resp = client.get(f"/api/projects/{project_id}/progress")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["eligible_scorer_count"] == 1
    assert body["incomplete_scorer_count"] == 2
    assert len(body["scorers"]) == 3
    assert len(body["scorers"][0]["statuses"]) == 3  # 3 subjects


# ---------------------------------------------------------------------------
# 集計 / competition ranking
# ---------------------------------------------------------------------------


def test_aggregation_computes_total_mean_and_criterion_average(client, app, db):
    data = _create_and_start(client)
    project_id = data["project_id"]

    scorer1 = app.test_client()
    _login_scorer(scorer1, data["scorers"][0]["code"])
    _submit_all(scorer1, project_id, score_value=20)  # 5 criteria x 20 = 100

    scorer2 = app.test_client()
    _login_scorer(scorer2, data["scorers"][1]["code"])
    _submit_all(scorer2, project_id, score_value=10)  # 5 criteria x 10 = 50

    project = db.session.get(Project, project_id)
    project_service.transition_to_locked(project)

    summary = result_service.build_result_summary(project)
    assert summary["eligible_scorer_count"] == 2
    assert summary["theoretical_max_total"] == 100

    for subject_result in summary["subjects"]:
        assert subject_result["scorer_count"] == 2
        assert subject_result["total_score"] == 150  # 100 + 50
        assert subject_result["mean_score"] == 75.0
        for avg in subject_result["criterion_averages"]:
            assert avg["average"] == 15.0  # (20+10)/2


def test_competition_ranking_handles_ties(client, app, db):
    data = _create_and_start(client)
    project_id = data["project_id"]

    subjects = Subject.query.filter_by(project_id=project_id).order_by(Subject.sort_order).all()

    scorer1 = app.test_client()
    _login_scorer(scorer1, data["scorers"][0]["code"])
    criteria = Criterion.query.filter_by(project_id=project_id).order_by(Criterion.sort_order).all()
    dashboard = scorer1.get("/api/scorer/me/evaluations").get_json()

    # subjects[0]とsubjects[1]を同点(20点)、subjects[2]を低得点(10点)にする
    score_plan = {subjects[0].id: 20, subjects[1].id: 20, subjects[2].id: 10}
    for row in dashboard["subjects"]:
        value = score_plan[row["subject_id"]]
        scores = {str(c.id): value for c in criteria}
        scorer1.post(f"/api/evaluations/{row['evaluation_id']}/scores", json={"scores": scores})
        scorer1.post(f"/api/evaluations/{row['evaluation_id']}/submit")

    project = db.session.get(Project, project_id)
    project_service.transition_to_locked(project)

    summary = result_service.build_result_summary(project)
    ranks = {r["id"]: r["rank"] for r in summary["subjects"]}
    assert ranks[subjects[0].id] == 1
    assert ranks[subjects[1].id] == 1
    assert ranks[subjects[2].id] == 3  # 同点2件の次は3位(2位は欠番)


def test_result_summary_does_not_leak_other_project_data(client, app, db):
    data_a = _create_and_start(client, dict(VALID_PAYLOAD, name="プロジェクトA"))
    client_b = app.test_client()
    data_b = _create_and_start(client_b, dict(VALID_PAYLOAD, name="プロジェクトB"))

    scorer_a = app.test_client()
    _login_scorer(scorer_a, data_a["scorers"][0]["code"])
    _submit_all(scorer_a, data_a["project_id"], score_value=20)

    scorer_b = app.test_client()
    _login_scorer(scorer_b, data_b["scorers"][0]["code"])
    _submit_all(scorer_b, data_b["project_id"], score_value=5)

    project_a = db.session.get(Project, data_a["project_id"])
    project_service.transition_to_locked(project_a)

    summary_a = result_service.build_result_summary(project_a)
    subject_names_a = {s["name"] for s in summary_a["subjects"]}
    # プロジェクトBのSubject名("チームA"等)は共通命名だが、件数と対象が
    # プロジェクトAのSubjectのみであることを確認する
    assert len(summary_a["subjects"]) == 3
    assert all(s["total_score"] == 100 for s in summary_a["subjects"])  # Aのみのスコア(20x5)
