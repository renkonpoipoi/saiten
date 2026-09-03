"""Phase 2〜5を通しで検証するend-to-end統合テスト。

Project作成 -> Host認証 -> DRAFT->SCORING -> Scorer認証 -> 全Subject採点
-> Submit -> Host締切(forced close) -> LOCKED -> PRESENTING -> Result
-> FINISHED、という一連のフローを、複数Scorer・複数Subjectで検証する。
eligible scorerによる部分提出者の除外とcompetition rankingのtieも
このテスト内で確認する。
"""

from __future__ import annotations

from app.models import Criterion, Project, Subject
from app.services.code_service import hash_code

PAYLOAD = {
    "name": "統合テストプロジェクト",
    "subjects": ["チームA", "チームB", "チームC"],
    "scorers": ["採点者1", "採点者2", "採点者3"],
    "criteria": ["独創性", "実用性", "デザイン", "技術力", "拡張性"],
    "allow_host_scoring": False,
}


def _score_all_criteria(scorer_client, evaluation_id, value, criteria):
    scores = {str(c.id): value for c in criteria}
    resp = scorer_client.post(
        f"/api/evaluations/{evaluation_id}/scores", json={"scores": scores}
    )
    assert resp.status_code == 200
    resp = scorer_client.post(f"/api/evaluations/{evaluation_id}/submit")
    assert resp.status_code == 200


def test_full_lifecycle_with_forced_close_and_tie_ranking(client, app, db):
    # --- 1. プロジェクト作成 ---
    create_resp = client.post("/api/projects", json=PAYLOAD)
    assert create_resp.status_code == 201
    created = create_resp.get_json()
    project_id = created["project_id"]

    # DBには平文コードが一切残らないこと
    project = db.session.get(Project, project_id)
    assert project.host_code_hash == hash_code(created["host_code"])
    assert created["host_code"] not in project.host_code_hash

    # --- 2. Host認証 ---
    login_resp = client.post("/api/auth/host-login", json={"code": created["host_code"]})
    assert login_resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess["host_project_id"] == project_id

    # --- 3. DRAFT -> SCORING (Evaluation一括生成) ---
    start_resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"}
    )
    assert start_resp.status_code == 200
    assert db.session.get(Project, project_id).status == "SCORING"

    subjects = Subject.query.filter_by(project_id=project_id).order_by(Subject.sort_order).all()
    criteria = Criterion.query.filter_by(project_id=project_id).order_by(Criterion.sort_order).all()
    scorer1_code, scorer2_code, scorer3_code = (s["code"] for s in created["scorers"])

    # --- 4. Scorer認証(3名、それぞれ独立したセッション) ---
    scorer1 = app.test_client()
    scorer2 = app.test_client()
    scorer3 = app.test_client()
    assert scorer1.post("/api/auth/scorer-login", json={"code": scorer1_code}).status_code == 200
    assert scorer2.post("/api/auth/scorer-login", json={"code": scorer2_code}).status_code == 200
    assert scorer3.post("/api/auth/scorer-login", json={"code": scorer3_code}).status_code == 200

    def evaluation_id_for(scorer_client, subject_id):
        dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
        return next(r["evaluation_id"] for r in dashboard["subjects"] if r["subject_id"] == subject_id)

    # --- 5. 全Subject採点 + Submit ---
    # scorer1: 全チームに20点 x5criteria = 100点/チーム (eligible)
    for subject in subjects:
        eid = evaluation_id_for(scorer1, subject.id)
        _score_all_criteria(scorer1, eid, 20, criteria)

    # scorer2: 全チームに10点 x5criteria = 50点/チーム。ただしチームCだけ
    # 低めの5点にしてtieを作る(eligible)
    for subject in subjects:
        eid = evaluation_id_for(scorer2, subject.id)
        value = 5 if subject.name == "チームC" else 10
        _score_all_criteria(scorer2, eid, value, criteria)

    # scorer3: チームAだけ提出、チームB/Cは未提出のまま(non-eligible)
    subject_a = next(s for s in subjects if s.name == "チームA")
    eid_a_scorer3 = evaluation_id_for(scorer3, subject_a.id)
    _score_all_criteria(scorer3, eid_a_scorer3, 20, criteria)

    # --- 6. Host締切(forced close: scorer3が未完了のまま締切る) ---
    progress_before = client.get(f"/api/projects/{project_id}/progress").get_json()
    assert progress_before["eligible_scorer_count"] == 2
    assert progress_before["incomplete_scorer_count"] == 1

    lock_resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"}
    )
    assert lock_resp.status_code == 200
    assert db.session.get(Project, project_id).status == "LOCKED"

    # LOCKED以降、Scorerからの書き込みは全て拒否される
    reject_resp = scorer1.post(
        f"/api/evaluations/{evaluation_id_for(scorer1, subject_a.id)}/scores",
        json={"scores": {}},
    )
    assert reject_resp.status_code == 409

    # --- 7. PRESENTING ---
    present_resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "PRESENTING"}
    )
    assert present_resp.status_code == 200

    # --- 8. Result取得 ---
    summary_resp = client.get(f"/api/projects/{project_id}/result-summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.get_json()

    assert summary["eligible_scorer_count"] == 2  # scorer3は除外
    by_name = {s["name"]: s for s in summary["subjects"]}

    # scorer3のデータが混入していれば200点になってしまうが、除外されるため
    # scorer1(20)+scorer2(10) = 30点/criterion x5 = 150点のみ
    assert by_name["チームA"]["total_score"] == 150
    assert by_name["チームA"]["scorer_count"] == 2
    # チームB: scorer1(20)+scorer2(10) = 150点(Aと同点)
    assert by_name["チームB"]["total_score"] == 150
    # チームC: scorer1(20)+scorer2(5) = 125点(最下位)
    assert by_name["チームC"]["total_score"] == 125

    # competition ranking: A,Bが同点1位、Cが3位(2位は欠番)
    assert by_name["チームA"]["rank"] == 1
    assert by_name["チームB"]["rank"] == 1
    assert by_name["チームC"]["rank"] == 3

    # criterion別平均: (20+10)/2 = 15.0 (チームA/B)
    for avg in by_name["チームA"]["criterion_averages"]:
        assert avg["average"] == 15.0
    assert by_name["チームA"]["mean_score"] == 75.0  # 150/2

    # --- 9. FINISHED ---
    finish_resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "FINISHED"}
    )
    assert finish_resp.status_code == 200
    assert db.session.get(Project, project_id).status == "FINISHED"

    # FINISHED後もHostは結果を再閲覧できる(同じ集計結果が得られる)
    final_resp = client.get(f"/api/projects/{project_id}/result-summary")
    assert final_resp.status_code == 200
    final_summary = final_resp.get_json()
    assert final_summary["project"]["status"] == "FINISHED"
    final_by_name = {s["name"]: s for s in final_summary["subjects"]}
    assert final_by_name["チームA"]["total_score"] == 150
    assert final_by_name["チームA"]["rank"] == 1
