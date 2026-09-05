"""Phase 10B-2: 被採点者・採点者の手動並び替え。

本番の発表順と審査員の座席順は当日まで決まらないため、DRAFT中に自由に
並び替えられるようにする。順位計算(competition ranking)には影響させない。

subjects には UNIQUE(project_id, sort_order) があるため、素直な代入は必ず
IntegrityError になる。service 側の2段階代入がそれを回避していることを、
実際に並び替えを走らせて確認する。
"""

from __future__ import annotations

from pathlib import Path

from app.models import Project, Scorer, Subject
from tests.helpers import (
    create_project,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"

FOUR = {"subjects": ["A", "B", "C", "D"], "scorers": ["吉田", "田中", "佐藤", "山田"]}


def _create(client, **overrides):
    payload = dict(FOUR)
    payload.update(overrides)
    return create_project(client, **payload)


def _subject_names(project_id):
    return [
        s.name
        for s in Subject.query.filter_by(project_id=project_id)
        .order_by(Subject.sort_order, Subject.id)
        .all()
    ]


def _subject_orders(project_id):
    return [
        s.sort_order
        for s in Subject.query.filter_by(project_id=project_id)
        .order_by(Subject.sort_order, Subject.id)
        .all()
    ]


def _scorer_names(project_id):
    return [
        s.display_name
        for s in Scorer.query.filter_by(project_id=project_id, is_active=True)
        .order_by(Scorer.sort_order, Scorer.id)
        .all()
    ]


def _scorer_orders(project_id):
    return [
        s.sort_order
        for s in Scorer.query.filter_by(project_id=project_id, is_active=True)
        .order_by(Scorer.sort_order, Scorer.id)
        .all()
    ]


def _ids_by_name(project_id, names):
    lookup = {
        s.name: s.id for s in Subject.query.filter_by(project_id=project_id).all()
    }
    return [lookup[n] for n in names]


def _reorder_subjects(client, project_id, names):
    return client.patch(
        f"/api/projects/{project_id}/subjects/order",
        json={"subject_ids": _ids_by_name(project_id, names)},
    )


def _scorer_ids_by_name(project_id, names):
    lookup = {
        s.display_name: s.id
        for s in Scorer.query.filter_by(project_id=project_id).all()
    }
    return [lookup[n] for n in names]


def _reorder_scorers(client, project_id, names):
    return client.patch(
        f"/api/projects/{project_id}/scorers/order",
        json={"scorer_ids": _scorer_ids_by_name(project_id, names)},
    )


# ---------------------------------------------------------------------------
# 初期状態
# ---------------------------------------------------------------------------


def test_creation_assigns_contiguous_orders(client, db):
    created = _create(client)
    pid = created["project_id"]
    assert _subject_names(pid) == ["A", "B", "C", "D"]
    assert _subject_orders(pid) == [0, 1, 2, 3]
    assert _scorer_names(pid) == ["吉田", "田中", "佐藤", "山田"]
    assert _scorer_orders(pid) == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# Subject の並び替え
# ---------------------------------------------------------------------------


def test_subject_reorder_survives_the_unique_constraint(client, db):
    """★ UNIQUE(project_id, sort_order) 下でも並び替えが成功すること。"""
    created = _create(client)
    pid = created["project_id"]

    assert _reorder_subjects(client, pid, ["C", "A", "D", "B"]).status_code == 200
    assert _subject_names(pid) == ["C", "A", "D", "B"]
    assert _subject_orders(pid) == [0, 1, 2, 3]


def test_subject_reorder_handles_a_simple_swap(client, db):
    """隣接2件の入れ替え(素直な代入だと必ず衝突するケース)。"""
    created = _create(client)
    pid = created["project_id"]
    assert _reorder_subjects(client, pid, ["B", "A", "C", "D"]).status_code == 200
    assert _subject_names(pid) == ["B", "A", "C", "D"]


def test_subject_reorder_handles_a_full_reversal(client, db):
    created = _create(client)
    pid = created["project_id"]
    assert _reorder_subjects(client, pid, ["D", "C", "B", "A"]).status_code == 200
    assert _subject_names(pid) == ["D", "C", "B", "A"]
    assert _subject_orders(pid) == [0, 1, 2, 3]


def test_subject_reorder_is_idempotent(client, db):
    created = _create(client)
    pid = created["project_id"]
    for _ in range(3):
        assert _reorder_subjects(client, pid, ["C", "A", "D", "B"]).status_code == 200
    assert _subject_names(pid) == ["C", "A", "D", "B"]


def test_subject_reorder_response_reflects_the_new_order(client, db):
    created = _create(client)
    pid = created["project_id"]
    body = _reorder_subjects(client, pid, ["C", "A", "D", "B"]).get_json()
    assert [s["name"] for s in body["subjects"]] == ["C", "A", "D", "B"]
    assert [s["sort_order"] for s in body["subjects"]] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# Scorer の並び替え
# ---------------------------------------------------------------------------


def test_scorer_reorder_matches_the_seating_order(client, db):
    created = _create(client)
    pid = created["project_id"]
    assert _reorder_scorers(client, pid, ["田中", "吉田", "山田", "佐藤"]).status_code == 200
    assert _scorer_names(pid) == ["田中", "吉田", "山田", "佐藤"]
    assert _scorer_orders(pid) == [0, 1, 2, 3]


def test_scorer_reorder_ignores_host_scorer_role(client, db):
    """★ ホスト兼任かどうかは並び順に一切影響しない。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=1)
    pid = created["project_id"]
    # 兼任の田中を末尾へ
    assert _reorder_scorers(client, pid, ["吉田", "佐藤", "山田", "田中"]).status_code == 200
    assert _scorer_names(pid) == ["吉田", "佐藤", "山田", "田中"]

    host = [s for s in Scorer.query.filter_by(project_id=pid).all() if s.is_host_scorer]
    assert [s.display_name for s in host] == ["田中"]
    assert host[0].sort_order == 3

    # 先頭へも戻せる
    assert _reorder_scorers(client, pid, ["田中", "吉田", "佐藤", "山田"]).status_code == 200
    assert _scorer_names(pid) == ["田中", "吉田", "佐藤", "山田"]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_reorder_rejects_missing_ids(client, db):
    created = _create(client)
    pid = created["project_id"]
    ids = _ids_by_name(pid, ["A", "B", "C"])          # D が欠落
    resp = client.patch(f"/api/projects/{pid}/subjects/order", json={"subject_ids": ids})
    assert resp.status_code == 400
    assert _subject_names(pid) == ["A", "B", "C", "D"]


def test_reorder_rejects_duplicate_ids(client, db):
    created = _create(client)
    pid = created["project_id"]
    ids = _ids_by_name(pid, ["A", "B", "C"])
    resp = client.patch(
        f"/api/projects/{pid}/subjects/order", json={"subject_ids": ids + [ids[0]]}
    )
    assert resp.status_code == 400
    assert _subject_names(pid) == ["A", "B", "C", "D"]


def test_reorder_rejects_foreign_ids(client, app, db):
    mine = _create(client)
    other = app.test_client()
    theirs = _create(other, name="他人の")
    foreign = _ids_by_name(theirs["project_id"], ["A"])[0]

    ids = _ids_by_name(mine["project_id"], ["A", "B", "C"]) + [foreign]
    resp = client.patch(
        f"/api/projects/{mine['project_id']}/subjects/order", json={"subject_ids": ids}
    )
    assert resp.status_code == 400
    assert _subject_names(mine["project_id"]) == ["A", "B", "C", "D"]
    assert _subject_names(theirs["project_id"]) == ["A", "B", "C", "D"]


def test_reorder_rejects_non_integer_ids(client, db):
    created = _create(client)
    pid = created["project_id"]
    for bad in ([1, "2", 3, 4], [True, 2, 3, 4], "abc", {"a": 1}, None):
        resp = client.patch(
            f"/api/projects/{pid}/subjects/order", json={"subject_ids": bad}
        )
        assert resp.status_code == 400, repr(bad)
    assert _subject_names(pid) == ["A", "B", "C", "D"]


def test_reorder_requires_a_host_session(client, app, db):
    created = _create(client)
    pid = created["project_id"]
    ids = _ids_by_name(pid, ["C", "A", "D", "B"])
    anonymous = app.test_client()
    resp = anonymous.patch(
        f"/api/projects/{pid}/subjects/order", json={"subject_ids": ids}
    )
    assert resp.status_code == 403
    assert _subject_names(pid) == ["A", "B", "C", "D"]


def test_reorder_rejects_another_projects_host(client, app, db):
    other = app.test_client()
    theirs = _create(other, name="他人の")
    _create(client)
    ids = _ids_by_name(theirs["project_id"], ["C", "A", "D", "B"])
    resp = client.patch(
        f"/api/projects/{theirs['project_id']}/subjects/order", json={"subject_ids": ids}
    )
    assert resp.status_code == 403


def test_reorder_is_draft_only(client, db):
    """★ SCORING 以降は server 側で拒否する(UIのdisabledに依存しない)。"""
    created = _create(client)
    pid = created["project_id"]
    ids = _ids_by_name(pid, ["C", "A", "D", "B"])
    scorer_ids = _scorer_ids_by_name(pid, ["田中", "吉田", "佐藤", "山田"])
    start_scoring(client, pid)

    assert client.patch(
        f"/api/projects/{pid}/subjects/order", json={"subject_ids": ids}
    ).status_code == 409
    assert client.patch(
        f"/api/projects/{pid}/scorers/order", json={"scorer_ids": scorer_ids}
    ).status_code == 409
    assert _subject_names(pid) == ["A", "B", "C", "D"]
    assert _scorer_names(pid) == ["吉田", "田中", "佐藤", "山田"]


# ---------------------------------------------------------------------------
# 追加・削除での正規化
# ---------------------------------------------------------------------------


def test_deleting_a_subject_closes_the_gap(client, db):
    created = _create(client)
    pid = created["project_id"]
    victim = _ids_by_name(pid, ["B"])[0]

    assert client.delete(f"/api/projects/{pid}/subjects/{victim}").status_code == 204
    assert _subject_names(pid) == ["A", "C", "D"]
    assert _subject_orders(pid) == [0, 1, 2]


def test_adding_a_subject_appends_contiguously(client, db):
    created = _create(client)
    pid = created["project_id"]
    victim = _ids_by_name(pid, ["A"])[0]
    client.delete(f"/api/projects/{pid}/subjects/{victim}")

    assert client.post(f"/api/projects/{pid}/subjects", json={"name": "E"}).status_code == 201
    assert _subject_names(pid) == ["B", "C", "D", "E"]
    assert _subject_orders(pid) == [0, 1, 2, 3]


def test_deleting_a_scorer_closes_the_gap(client, db):
    created = _create(client)
    pid = created["project_id"]
    victim = _scorer_ids_by_name(pid, ["田中"])[0]

    assert client.delete(f"/api/projects/{pid}/scorers/{victim}").status_code == 204
    assert _scorer_names(pid) == ["吉田", "佐藤", "山田"]
    assert _scorer_orders(pid) == [0, 1, 2]


def test_adding_a_scorer_appends_at_the_end(client, db):
    created = _create(client)
    pid = created["project_id"]
    client.delete(f"/api/projects/{pid}/scorers/{_scorer_ids_by_name(pid, ['吉田'])[0]}")

    assert client.post(
        f"/api/projects/{pid}/scorers", json={"display_name": "鈴木"}
    ).status_code == 201
    assert _scorer_names(pid) == ["田中", "佐藤", "山田", "鈴木"]
    assert _scorer_orders(pid) == [0, 1, 2, 3]


def test_reorder_then_delete_then_add_stays_contiguous(client, db):
    created = _create(client)
    pid = created["project_id"]
    _reorder_subjects(client, pid, ["D", "C", "B", "A"])
    client.delete(f"/api/projects/{pid}/subjects/{_ids_by_name(pid, ['C'])[0]}")
    client.post(f"/api/projects/{pid}/subjects", json={"name": "E"})

    assert _subject_names(pid) == ["D", "B", "A", "E"]
    assert _subject_orders(pid) == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# 下流への反映
# ---------------------------------------------------------------------------


def test_subject_order_reaches_the_scorer_dashboard(client, app, db):
    created = _create(client)
    pid = created["project_id"]
    _reorder_subjects(client, pid, ["C", "A", "D", "B"])
    start_scoring(client, pid)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    rows = scorer.get("/api/scorer/me/evaluations").get_json()["subjects"]
    assert [r["subject_name"] for r in rows] == ["C", "A", "D", "B"]


def test_subject_order_reaches_the_host_dashboard(client, db):
    created = _create(client)
    pid = created["project_id"]
    _reorder_subjects(client, pid, ["C", "A", "D", "B"])
    start_scoring(client, pid)

    progress = client.get(f"/api/projects/{pid}/progress").get_json()
    assert [s["name"] for s in progress["subjects"]] == ["C", "A", "D", "B"]


def test_scorer_order_reaches_the_host_dashboard(client, db):
    created = _create(client)
    pid = created["project_id"]
    _reorder_scorers(client, pid, ["田中", "吉田", "山田", "佐藤"])
    start_scoring(client, pid)

    progress = client.get(f"/api/projects/{pid}/progress").get_json()
    assert [s["display_name"] for s in progress["scorers"]] == [
        "田中", "吉田", "山田", "佐藤"
    ]


def test_scorer_order_drives_the_judge_reveal_order(client, app, db):
    """★ 座席順が Phase 9 の Judge rail / 得点開示順になること。"""
    created = _create(client, subjects=["A"])
    pid = created["project_id"]
    _reorder_scorers(client, pid, ["田中", "吉田", "山田", "佐藤"])
    start_scoring(client, pid)

    for info in created["scorers"]:
        s = app.test_client()
        login_scorer(s, info["code"])
        for subject in subjects_for(pid):
            score_and_submit(s, pid, subject.id)

    for target in ("LOCKED", "PRESENTING"):
        assert client.post(
            f"/api/projects/{pid}/transition", json={"target_status": target}
        ).status_code == 200

    summary = client.get(f"/api/projects/{pid}/result-summary").get_json()
    judges = [j["display_name"] for j in summary["subjects"][0]["judge_totals"]]
    assert judges == ["田中", "吉田", "山田", "佐藤"]


def test_sequential_progression_follows_the_manual_order(client, app, db):
    """SEQUENTIAL の進行順が並び替え後の順になること。"""
    created = _create(client, presentation_mode="SEQUENTIAL", scorers=["s1"])
    pid = created["project_id"]
    _reorder_subjects(client, pid, ["C", "A", "D", "B"])
    start_scoring(client, pid)

    state = client.get(f"/api/projects/{pid}/presentation-state").get_json()
    current = [s for s in state["subjects"] if s["id"] == state["current_subject_id"]][0]
    assert current["name"] == "C"


def test_reorder_does_not_change_ranking(client, app, db):
    """並び替えは順位計算に影響しない。"""
    created = _create(client, subjects=["A", "B"], scorers=["s1"])
    pid = created["project_id"]
    start_scoring(client, pid)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    subs = {s.name: s for s in subjects_for(pid)}
    score_and_submit(scorer, pid, subs["A"].id, value=20)
    score_and_submit(scorer, pid, subs["B"].id, value=10)

    for target in ("LOCKED", "PRESENTING"):
        client.post(f"/api/projects/{pid}/transition", json={"target_status": target})
    ranks = {
        s["name"]: s["rank"]
        for s in client.get(f"/api/projects/{pid}/result-summary").get_json()["subjects"]
    }
    assert ranks == {"A": 1, "B": 2}


# ---------------------------------------------------------------------------
# 実装契約
# ---------------------------------------------------------------------------


def test_normalization_uses_two_phase_assignment():
    """★ 退避 -> flush -> 最終値 の順序が保たれていること。"""
    source = (APP_DIR / "services" / "project_service.py").read_text(encoding="utf-8")
    body = source[source.index("def _normalize_sort_order("):]
    body = body[: body.index("\ndef ")]

    park = body.index("row.sort_order = -(index + 1)")
    flush = body.index("db.session.flush()")
    final = body.index("row.sort_order = index")
    assert park < flush < final
