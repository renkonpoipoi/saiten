"""Phase 10B-7: 本番の進行順 (event_order) をアプリ全体へ伝播する。

event_order は RANDOM_DRAW なら抽選順、MANUAL なら Host が並べた順。
**出してよいのは発表可能な範囲のendpointだけ**(result-summary /
interim-ranking / subjects/<id>/result)。抽選前に触れるどのpayloadにも
含めない。
"""

from __future__ import annotations

from pathlib import Path

from app.models import Subject
from tests.helpers import (
    create_project,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"

FOUR = {"subjects": ["A", "B", "C", "D"], "scorers": ["s1"]}


def _create(client, mode="RANDOM_DRAW", **overrides):
    payload = dict(FOUR)
    payload.update(overrides)
    created = create_project(client, **payload)
    if mode != "MANUAL":
        resp = client.patch(
            f"/api/projects/{created['project_id']}/subject-order-mode",
            json={"subject_order_mode": mode},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
    return created


def _secret_order(project_id: int) -> list[str]:
    rows = (
        Subject.query.filter_by(project_id=project_id)
        .order_by(Subject.draw_order)
        .all()
    )
    return [s.name for s in rows]


def _draw_all(client, project_id: int) -> list[str]:
    names = []
    while True:
        progress = client.get(f"/api/projects/{project_id}/progress").get_json()
        draw = progress["draw"]
        if draw["remaining_count"] == 0:
            return names
        resp = client.post(
            f"/api/projects/{project_id}/draw-next-subject",
            json={"expected_cursor": draw["draw_cursor"]},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        names.append(resp.get_json()["subject"]["name"])


def _batch_to_locked(client, app, created, mode="RANDOM_DRAW"):
    """BATCH で採点を終え、全件抽選してから LOCKED まで進める。"""
    pid = created["project_id"]
    start_scoring(client, pid)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    for subject in subjects_for(pid):
        score_and_submit(scorer, pid, subject.id)

    drawn = _draw_all(client, pid) if mode == "RANDOM_DRAW" else []
    resp = client.post(f"/api/projects/{pid}/transition", json={"target_status": "LOCKED"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return pid, drawn


# ---------------------------------------------------------------------------
# event_order の値
# ---------------------------------------------------------------------------


def test_result_summary_exposes_the_draw_order_as_event_order(client, app, db):
    created = _create(client)
    pid, drawn = _batch_to_locked(client, app, created)

    summary = client.get(f"/api/projects/{pid}/result-summary").get_json()
    by_event = sorted(summary["subjects"], key=lambda r: r["event_order"])
    assert [r["name"] for r in by_event] == drawn
    assert [r["event_order"] for r in by_event] == [0, 1, 2, 3]


def test_event_order_matches_the_secret_sequence(client, app, db):
    created = _create(client)
    pid, _ = _batch_to_locked(client, app, created)
    secret = _secret_order(pid)

    summary = client.get(f"/api/projects/{pid}/result-summary").get_json()
    by_event = sorted(summary["subjects"], key=lambda r: r["event_order"])
    assert [r["name"] for r in by_event] == secret


def test_manual_event_order_is_the_display_order(client, app, db):
    created = _create(client, mode="MANUAL")
    pid = created["project_id"]
    subject_ids = [s.id for s in subjects_for(pid)]
    reordered = list(reversed(subject_ids))
    assert client.patch(
        f"/api/projects/{pid}/subjects/order", json={"subject_ids": reordered}
    ).status_code == 200

    pid, _ = _batch_to_locked(client, app, created, mode="MANUAL")
    summary = client.get(f"/api/projects/{pid}/result-summary").get_json()
    by_event = sorted(summary["subjects"], key=lambda r: r["event_order"])
    assert [r["id"] for r in by_event] == reordered
    assert all(r["event_order"] == r["sort_order"] for r in summary["subjects"])


def test_event_order_differs_from_sort_order_at_least_sometimes(client, app, db):
    """抽選順と表示順が同じとは限らない(=別の値として扱われている)。"""
    seen_difference = False
    for _ in range(12):
        created = _create(client)
        pid, _ = _batch_to_locked(client, app, created)
        summary = client.get(f"/api/projects/{pid}/result-summary").get_json()
        if any(r["event_order"] != r["sort_order"] for r in summary["subjects"]):
            seen_difference = True
            break
    assert seen_difference


def test_subject_result_and_interim_ranking_carry_event_order(client, app, db):
    created = _create(client, presentation_mode="SEQUENTIAL")
    pid = created["project_id"]
    start_scoring(client, pid)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])

    seen = []
    for cursor in range(4):
        body = client.post(
            f"/api/projects/{pid}/draw-next-subject", json={"expected_cursor": cursor}
        ).get_json()
        subject_id = body["subject"]["id"]
        seen.append(body["subject"]["name"])

        score_and_submit(scorer, pid, subject_id)
        client.post(f"/api/projects/{pid}/subjects/{subject_id}/lock")

        detail = client.get(
            f"/api/projects/{pid}/subjects/{subject_id}/result"
        ).get_json()
        assert detail["subject"]["event_order"] == cursor

        interim = client.get(f"/api/projects/{pid}/interim-ranking").get_json()
        assert sorted(r["event_order"] for r in interim["subjects"]) == list(
            range(cursor + 1)
        )
        assert {r["name"] for r in interim["subjects"]} == set(seen)

        client.post(f"/api/projects/{pid}/subjects/{subject_id}/present")


# ---------------------------------------------------------------------------
# 露出の境界
# ---------------------------------------------------------------------------


def test_event_order_is_absent_from_pre_reveal_payloads(client, db):
    """★ 抽選前に触れるpayloadに event_order を含めない。"""
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)

    for path in [
        f"/api/projects/{pid}",
        f"/api/projects/{pid}/progress",
        f"/api/projects/{pid}/presentation-state",
    ]:
        text = client.get(path).get_data(as_text=True)
        assert "event_order" not in text, path
        assert "draw_order" not in text, path


def test_analysis_and_exports_do_not_gain_event_order(client, app, db):
    """分析・書き出しは順位順のまま。今回の変更で列を増やさない。"""
    created = _create(client)
    pid, _ = _batch_to_locked(client, app, created)

    for path in [
        f"/api/projects/{pid}/analysis",
        f"/api/projects/{pid}/export.csv",
        f"/api/projects/{pid}/export.md",
    ]:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "event_order" not in resp.get_data(as_text=True), path


def test_event_order_is_opt_in_per_endpoint():
    source = (APP_DIR / "services" / "result_service.py").read_text(encoding="utf-8")
    assert source.count("with_event_order=True") == 3
    assert "with_event_order: bool = False" in source


# ---------------------------------------------------------------------------
# ランキング自体は順位順のまま
# ---------------------------------------------------------------------------


def test_ranking_is_still_ordered_by_score_not_by_draw(client, app, db):
    created = _create(client, subjects=["A", "B", "C"], scorers=["s1"])
    pid = created["project_id"]
    start_scoring(client, pid)

    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    subjects = subjects_for(pid)
    for value, subject in zip([10, 20, 15], subjects):
        score_and_submit(scorer, pid, subject.id, value=value)

    _draw_all(client, pid)
    client.post(f"/api/projects/{pid}/transition", json={"target_status": "LOCKED"})

    summary = client.get(f"/api/projects/{pid}/result-summary").get_json()
    totals = [r["total_score"] for r in summary["subjects"]]
    assert totals == sorted(totals, reverse=True)
    assert [r["rank"] for r in summary["subjects"]] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 得点発表の順番は client が決めない
# ---------------------------------------------------------------------------


def test_presentation_orders_subjects_by_event_order():
    js = (APP_DIR / "static" / "js" / "result_presentation.js").read_text(encoding="utf-8")
    body = js[js.index("function orderedSubjects(summary) {"):]
    body = body[: body.index("\n  }")]
    assert "core.orderByEvent(summary.subjects)" in body
    assert "sort_order" not in body


def test_judge_order_follows_the_seat_order(client, app, db):
    """Judge rail / 得点開示順は Scorer.sort_order に従う。"""
    created = _create(client, mode="MANUAL", scorers=["s1", "s2", "s3"])
    pid = created["project_id"]
    scorers = client.get(f"/api/projects/{pid}").get_json()["scorers"]
    reordered = list(reversed([s["id"] for s in scorers]))
    assert client.patch(
        f"/api/projects/{pid}/scorers/order", json={"scorer_ids": reordered}
    ).status_code == 200

    start_scoring(client, pid)
    for scorer in created["scorers"]:
        c = app.test_client()
        login_scorer(c, scorer["code"])
        for subject in subjects_for(pid):
            score_and_submit(c, pid, subject.id)
    client.post(f"/api/projects/{pid}/transition", json={"target_status": "LOCKED"})

    summary = client.get(f"/api/projects/{pid}/result-summary").get_json()
    for row in summary["subjects"]:
        assert [j["scorer_id"] for j in row["judge_totals"]] == reordered
