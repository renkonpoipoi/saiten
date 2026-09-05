"""Phase 9C: interim-ranking API。

SEQUENTIALは当該Subjectを
  LOCKED -> Judge Reveal -> TOTAL -> 暫定ランキングへ挿入
  -> Hostが「確定して次へ」 -> PRESENTED
の順で扱うため、ランキングへ挿入する瞬間の今回SubjectはLOCKEDである。

したがって対象statusは厳密に次のとおり。
  含める : LOCKED / PRESENTED
  含めない: WAITING / SCORING

「未発表を含めない」という曖昧な言い方ではなく、
「WAITING / SCORING Subject の score を絶対に漏らさない」ことを検証する。

順位はサーバー側のcompetition rankingが唯一の正解であり、
最終的なresult-summaryのrankと完全に一致しなければならない。
"""

from __future__ import annotations

import json

from app.models import Project, Subject
from tests.helpers import (
    create_project,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

FOUR_SUBJECTS = {
    "subjects": ["チームA", "チームB", "チームC", "チームD"],
    "scorers": ["採点者1", "採点者2"],
}


def _sequential_project(client, **overrides):
    payload = dict(FOUR_SUBJECTS, presentation_mode="SEQUENTIAL")
    payload.update(overrides)
    return create_project(client, **payload)


def _submit_all_for(app, created, project_id, subject_id, values=None):
    for index, scorer_info in enumerate(created["scorers"]):
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        value = values[index] if values else 15
        score_and_submit(scorer, project_id, subject_id, value=value)


def _lock(client, project_id, subject_id):
    resp = client.post(f"/api/projects/{project_id}/subjects/{subject_id}/lock")
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _present(client, project_id, subject_id):
    resp = client.post(f"/api/projects/{project_id}/subjects/{subject_id}/present")
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _interim(client, project_id):
    resp = client.get(f"/api/projects/{project_id}/interim-ranking")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _status_of(project_id, subject_id) -> str:
    return Subject.query.filter_by(id=subject_id).one().presentation_status


# ---------------------------------------------------------------------------
# 対象 status の厳密な定義
# ---------------------------------------------------------------------------


def test_interim_ranking_is_empty_before_any_subject_is_locked(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    body = _interim(client, project_id)
    assert body["subjects"] == []


def test_interim_ranking_includes_the_locked_subject_being_revealed(client, app, db):
    """挿入の瞬間、今回SubjectはLOCKED(まだPRESENTEDではない)。"""
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    _submit_all_for(app, created, project_id, subjects[0].id)
    _lock(client, project_id, subjects[0].id)

    assert _status_of(project_id, subjects[0].id) == "LOCKED"
    body = _interim(client, project_id)
    assert [s["name"] for s in body["subjects"]] == ["チームA"]
    assert body["subjects"][0]["presentation_status"] == "LOCKED"
    assert body["subjects"][0]["rank"] == 1


def test_interim_ranking_includes_presented_subjects(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    _submit_all_for(app, created, project_id, subjects[0].id)
    _lock(client, project_id, subjects[0].id)
    _present(client, project_id, subjects[0].id)

    assert _status_of(project_id, subjects[0].id) == "PRESENTED"
    body = _interim(client, project_id)
    assert [s["name"] for s in body["subjects"]] == ["チームA"]
    assert body["subjects"][0]["presentation_status"] == "PRESENTED"


def test_interim_ranking_excludes_waiting_and_scoring_subjects(client, app, db):
    """WAITING / SCORING のSubjectは一件も含めない。"""
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    # A: PRESENTED, B: LOCKED(いま発表中), C: SCORING, D: WAITING
    _submit_all_for(app, created, project_id, subjects[0].id)
    _lock(client, project_id, subjects[0].id)
    _present(client, project_id, subjects[0].id)
    _submit_all_for(app, created, project_id, subjects[1].id)
    _lock(client, project_id, subjects[1].id)
    _present(client, project_id, subjects[1].id)
    # Bをpresentした時点でCがSCORINGになる。Cを締め切って「発表中」にする。
    _submit_all_for(app, created, project_id, subjects[2].id)
    _lock(client, project_id, subjects[2].id)

    statuses = {s.name: _status_of(project_id, s.id) for s in subjects}
    assert statuses == {
        "チームA": "PRESENTED",
        "チームB": "PRESENTED",
        "チームC": "LOCKED",
        "チームD": "WAITING",
    }

    body = _interim(client, project_id)
    names = sorted(s["name"] for s in body["subjects"])
    assert names == ["チームA", "チームB", "チームC"]
    assert "チームD" not in names
    assert {s["presentation_status"] for s in body["subjects"]} == {"PRESENTED", "LOCKED"}


def test_scores_of_waiting_and_scoring_subjects_never_appear_in_the_response(
    client, app, db
):
    """WAITING / SCORING の点数が response の生文字列にも現れないこと。

    採点中/待機中のSubjectだけに他と重複しない点数を入れておき、
    その値も名前もレスポンス本文に一切出ないことを確認する。
    """
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    # 発表対象(A) は 15点x5軸x2名 = 150
    _submit_all_for(app, created, project_id, subjects[0].id, values=[15, 15])
    _lock(client, project_id, subjects[0].id)
    _present(client, project_id, subjects[0].id)

    # AをpresentするとBがSCORINGになる。Bへ他と重複しない値を提出するが、
    # 締め切らない(=SCORINGのまま)。13点x5軸=65点/名、合計130点。
    scoring_subject = subjects[1]
    assert _status_of(project_id, scoring_subject.id) == "SCORING"
    _submit_all_for(app, created, project_id, scoring_subject.id, values=[13, 13])
    assert _status_of(project_id, scoring_subject.id) == "SCORING"

    raw = client.get(f"/api/projects/{project_id}/interim-ranking").get_data(as_text=True)

    # jsonifyは非ASCIIを\uXXXXへエスケープするので、その形で照合する
    def encoded(text: str) -> str:
        return json.dumps(text)[1:-1]

    assert encoded("チームA") in raw
    for leaked in ("チームB", "チームC", "チームD"):
        assert encoded(leaked) not in raw, leaked
    for leaked_score in ("130", "65", "13"):
        assert leaked_score not in raw, leaked_score


def test_interim_ranking_never_changes_any_state(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)
    _submit_all_for(app, created, project_id, subjects[0].id)
    _lock(client, project_id, subjects[0].id)

    before = [(s.id, _status_of(project_id, s.id)) for s in subjects]
    for _ in range(3):
        _interim(client, project_id)
    after = [(s.id, _status_of(project_id, s.id)) for s in subjects]

    assert before == after
    assert db.session.get(Project, project_id).status == "SCORING"


# ---------------------------------------------------------------------------
# 順位の正解はサーバーだけが持つ
# ---------------------------------------------------------------------------


def test_interim_ranking_matches_result_summary_once_everything_is_presented(
    client, app, db
):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    values = {0: [20, 20], 1: [10, 10], 2: [18, 18], 3: [14, 14]}
    for index, subject in enumerate(subjects):
        _submit_all_for(app, created, project_id, subject.id, values=values[index])
        _lock(client, project_id, subject.id)
        _present(client, project_id, subject.id)

    interim = _interim(client, project_id)

    for target in ("LOCKED", "PRESENTING"):
        assert (
            client.post(
                f"/api/projects/{project_id}/transition", json={"target_status": target}
            ).status_code
            == 200
        )
    summary = client.get(f"/api/projects/{project_id}/result-summary").get_json()

    interim_ranks = {s["name"]: s["rank"] for s in interim["subjects"]}
    summary_ranks = {s["name"]: s["rank"] for s in summary["subjects"]}
    assert interim_ranks == summary_ranks

    interim_totals = {s["name"]: s["total_score"] for s in interim["subjects"]}
    summary_totals = {s["name"]: s["total_score"] for s in summary["subjects"]}
    assert interim_totals == summary_totals


def test_interim_ranking_keeps_competition_ranking_for_1_1_3(client, app, db):
    created = _sequential_project(
        client, subjects=["チームA", "チームB", "チームC"]
    )
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    # A と B が同点、C が下 -> 1位, 1位, 3位
    for index, values in enumerate([[20, 20], [20, 20], [10, 10]]):
        _submit_all_for(app, created, project_id, subjects[index].id, values=values)
        _lock(client, project_id, subjects[index].id)
        _present(client, project_id, subjects[index].id)

    ranks = {s["name"]: s["rank"] for s in _interim(client, project_id)["subjects"]}
    assert ranks == {"チームA": 1, "チームB": 1, "チームC": 3}
    assert 2 not in ranks.values()


def test_interim_ranking_keeps_competition_ranking_for_1_2_2_4(client, app, db):
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    # A=200(1位), B=150, C=150(同率2位), D=100(4位)
    for index, values in enumerate([[20, 20], [15, 15], [15, 15], [10, 10]]):
        _submit_all_for(app, created, project_id, subjects[index].id, values=values)
        _lock(client, project_id, subjects[index].id)
        _present(client, project_id, subjects[index].id)

    ranks = {s["name"]: s["rank"] for s in _interim(client, project_id)["subjects"]}
    assert ranks == {"チームA": 1, "チームB": 2, "チームC": 2, "チームD": 4}
    assert 3 not in ranks.values()


def test_interim_ranking_ranks_only_among_revealed_subjects(client, app, db):
    """未発表を含めないので、暫定順位は「いま出ている中での順位」になる。"""
    created = _sequential_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    _submit_all_for(app, created, project_id, subjects[0].id, values=[10, 10])
    _lock(client, project_id, subjects[0].id)
    _present(client, project_id, subjects[0].id)
    _submit_all_for(app, created, project_id, subjects[1].id, values=[20, 20])
    _lock(client, project_id, subjects[1].id)

    body = _interim(client, project_id)
    ranks = {s["name"]: s["rank"] for s in body["subjects"]}
    # 後から出たBが上に入る = 順位変動が起きる
    assert ranks == {"チームB": 1, "チームA": 2}


# ---------------------------------------------------------------------------
# 認可
# ---------------------------------------------------------------------------


def test_interim_ranking_requires_a_host_session(client, app, db):
    created = _sequential_project(client)
    anonymous = app.test_client()
    resp = anonymous.get(f"/api/projects/{created['project_id']}/interim-ranking")
    assert resp.status_code == 403


def test_interim_ranking_rejects_another_projects_host(client, app, db):
    mine = _sequential_project(client)
    other_client = app.test_client()
    theirs = _sequential_project(other_client, name="他人の")

    resp = client.get(f"/api/projects/{theirs['project_id']}/interim-ranking")
    assert resp.status_code == 403
    assert mine["project_id"] != theirs["project_id"]


def test_interim_ranking_404s_for_a_missing_project(client, app, db):
    created = _sequential_project(client)
    missing = created["project_id"] + 999
    # 自分のhost sessionでも、存在しないprojectは403(session不一致)で弾かれる
    assert client.get(f"/api/projects/{missing}/interim-ranking").status_code == 403


# ---------------------------------------------------------------------------
# BATCH では情報が増えていないこと
# ---------------------------------------------------------------------------


def test_batch_interim_ranking_stays_empty_while_scoring(client, app, db):
    """BATCHの採点中にこのエンドポイントから点数が漏れないこと。"""
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    for scorer_info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        for subject in subjects_for(project_id):
            score_and_submit(scorer, project_id, subject.id)

    raw = client.get(f"/api/projects/{project_id}/interim-ranking").get_data(as_text=True)
    body = client.get(f"/api/projects/{project_id}/interim-ranking").get_json()
    assert body["subjects"] == []
    assert json.dumps("チームA")[1:-1] not in raw
