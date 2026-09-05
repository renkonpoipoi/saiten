"""Phase 10B-4 / 10B-5: 発表順モード・秘密順の生成・抽選 API。

秘密なのは Subject 名ではなく **順序**(draw_order / future permutation /
next Subject / Subject と未来 position の対応)である。Subject 名そのものは
Host が Settings で最初から知っているので、他の API が返してよい。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.models import Project, Subject
from tests.helpers import (
    create_project,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"

FOUR = {"subjects": ["A", "B", "C", "D"], "scorers": ["s1", "s2"]}


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


def _draw_orders(project_id):
    return {
        s.name: s.draw_order
        for s in Subject.query.filter_by(project_id=project_id).all()
    }


def _cursor(db, project_id):
    return db.session.get(Project, project_id).draw_cursor


# ---------------------------------------------------------------------------
# 発表順モード
# ---------------------------------------------------------------------------


def test_projects_default_to_manual(client, db):
    created = create_project(client)
    detail = client.get(f"/api/projects/{created['project_id']}").get_json()
    assert detail["subject_order_mode"] == "MANUAL"


def test_order_mode_can_be_switched_while_draft(client, db):
    created = _create(client)
    detail = client.get(f"/api/projects/{created['project_id']}").get_json()
    assert detail["subject_order_mode"] == "RANDOM_DRAW"

    resp = client.patch(
        f"/api/projects/{created['project_id']}/subject-order-mode",
        json={"subject_order_mode": "MANUAL"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["subject_order_mode"] == "MANUAL"


def test_order_mode_rejects_unknown_values(client, db):
    created = create_project(client)
    for bad in ("RANDOM", "", None, 1, ["MANUAL"]):
        resp = client.patch(
            f"/api/projects/{created['project_id']}/subject-order-mode",
            json={"subject_order_mode": bad},
        )
        assert resp.status_code == 400, repr(bad)


def test_order_mode_is_draft_only(client, db):
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)
    resp = client.patch(
        f"/api/projects/{pid}/subject-order-mode", json={"subject_order_mode": "MANUAL"}
    )
    assert resp.status_code == 409
    assert db.session.get(Project, pid).subject_order_mode == "RANDOM_DRAW"


def test_order_mode_requires_host(client, app, db):
    created = _create(client)
    anonymous = app.test_client()
    resp = anonymous.patch(
        f"/api/projects/{created['project_id']}/subject-order-mode",
        json={"subject_order_mode": "MANUAL"},
    )
    assert resp.status_code == 403


def test_random_draw_still_allows_draft_editing(client, db):
    """★ DRAFT中は秘密順を作らないので、構成変更は通常どおりできる。"""
    created = _create(client)
    pid = created["project_id"]
    assert set(_draw_orders(pid).values()) == {None}

    assert client.post(f"/api/projects/{pid}/subjects", json={"name": "E"}).status_code == 201
    victim = [s for s in subjects_for(pid) if s.name == "A"][0]
    assert client.delete(f"/api/projects/{pid}/subjects/{victim.id}").status_code == 204
    assert client.patch(
        f"/api/projects/{pid}/subjects/{subjects_for(pid)[0].id}", json={"name": "B2"}
    ).status_code == 200
    assert set(_draw_orders(pid).values()) == {None}


# ---------------------------------------------------------------------------
# 秘密順の生成
# ---------------------------------------------------------------------------


def test_manual_never_generates_a_draw_sequence(client, db):
    created = _create(client, mode="MANUAL")
    pid = created["project_id"]
    start_scoring(client, pid)
    assert set(_draw_orders(pid).values()) == {None}
    assert _cursor(db, pid) == 0


def test_random_draw_generates_the_sequence_at_scoring(client, db):
    created = _create(client)
    pid = created["project_id"]
    assert set(_draw_orders(pid).values()) == {None}

    start_scoring(client, pid)

    orders = _draw_orders(pid)
    assert sorted(orders.values()) == [0, 1, 2, 3]
    assert _cursor(db, pid) == 0


def test_draw_sequence_is_generated_only_once(client, db):
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)
    first = _draw_orders(pid)

    # 前向きの遷移しか無く、再生成の経路が存在しない
    assert client.post(
        f"/api/projects/{pid}/transition", json={"target_status": "SCORING"}
    ).status_code == 409
    assert _draw_orders(pid) == first


def test_no_reroll_endpoint_exists():
    """★ 一度確定した秘密順を引き直す経路を作らない。"""
    from app import create_app

    app = create_app({"APP_ENV": "testing", "SECRET_KEY": "x"})
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert not any("reroll" in rule or "reshuffle" in rule for rule in rules)

    source = (APP_DIR / "services" / "project_service.py").read_text(encoding="utf-8")
    assert source.count("_generate_draw_sequence(") == 2  # 定義 + 呼び出し1箇所


def test_draw_sequence_uses_os_entropy():
    source = (APP_DIR / "services" / "project_service.py").read_text(encoding="utf-8")
    assert "secrets.SystemRandom().shuffle" in source
    assert "random.shuffle" not in source.replace("SystemRandom().shuffle", "")


def test_draw_sequence_is_not_always_the_display_order(client, db):
    """十分な試行で、秘密順が表示順と異なるケースが出ること(shuffleの実効性)。"""
    differs = False
    for index in range(12):
        created = _create(client, name=f"p{index}")
        pid = created["project_id"]
        start_scoring(client, pid)
        orders = [
            s.draw_order
            for s in Subject.query.filter_by(project_id=pid)
            .order_by(Subject.sort_order)
            .all()
        ]
        if orders != [0, 1, 2, 3]:
            differs = True
            break
    assert differs, "秘密順が常に表示順と同じになっている"


# ---------------------------------------------------------------------------
# SEQUENTIAL の初期状態
# ---------------------------------------------------------------------------


def test_sequential_random_draw_starts_with_everyone_waiting(client, db):
    """★ 先頭をSCORINGにすると「次は誰か」が漏れるので、全員WAITINGで始める。"""
    created = _create(client, presentation_mode="SEQUENTIAL", scorers=["s1"])
    pid = created["project_id"]
    start_scoring(client, pid)

    statuses = {s.name: s.presentation_status for s in subjects_for(pid)}
    assert set(statuses.values()) == {"WAITING"}

    state = client.get(f"/api/projects/{pid}/presentation-state").get_json()
    assert state["current_subject_id"] is None


def test_sequential_manual_still_starts_with_the_first_subject(client, db):
    """MANUAL の挙動は完全に据え置き。"""
    created = _create(client, mode="MANUAL", presentation_mode="SEQUENTIAL", scorers=["s1"])
    pid = created["project_id"]
    start_scoring(client, pid)

    statuses = [
        (s.name, s.presentation_status)
        for s in Subject.query.filter_by(project_id=pid).order_by(Subject.sort_order).all()
    ]
    assert statuses == [("A", "SCORING"), ("B", "WAITING"), ("C", "WAITING"), ("D", "WAITING")]


# ---------------------------------------------------------------------------
# LOCK 条件
# ---------------------------------------------------------------------------


def _submit_everything(app, created, project_id):
    for info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, info["code"])
        for subject in subjects_for(project_id):
            score_and_submit(scorer, project_id, subject.id)


def test_batch_random_draw_cannot_lock_before_every_draw(client, app, db):
    """★ 全件抽選する前に締め切らせない(発表順が確定しないため)。"""
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)
    _submit_everything(app, created, pid)

    resp = client.post(f"/api/projects/{pid}/transition", json={"target_status": "LOCKED"})
    assert resp.status_code == 409
    assert "draw every subject" in resp.get_json()["error"]
    assert db.session.get(Project, pid).status == "SCORING"

    # 途中まで抽選しても足りない
    for _ in range(2):
        client.post(f"/api/projects/{pid}/draw-next-subject",
                    json={"expected_cursor": _cursor(db, pid)})
    assert client.post(
        f"/api/projects/{pid}/transition", json={"target_status": "LOCKED"}
    ).status_code == 409


def test_batch_random_draw_can_lock_once_every_draw_is_done(client, app, db):
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)
    _submit_everything(app, created, pid)

    for _ in range(4):
        assert client.post(
            f"/api/projects/{pid}/draw-next-subject",
            json={"expected_cursor": _cursor(db, pid)},
        ).status_code == 200

    assert client.post(
        f"/api/projects/{pid}/transition", json={"target_status": "LOCKED"}
    ).status_code == 200
    assert db.session.get(Project, pid).status == "LOCKED"


def test_manual_lock_is_unchanged(client, app, db):
    """MANUAL は従来どおり、抽選の有無に関係なく締め切れる。"""
    created = _create(client, mode="MANUAL")
    pid = created["project_id"]
    start_scoring(client, pid)
    _submit_everything(app, created, pid)

    assert client.post(
        f"/api/projects/{pid}/transition", json={"target_status": "LOCKED"}
    ).status_code == 200


# ---------------------------------------------------------------------------
# draw_progress と秘匿境界(10B-4 時点)
# ---------------------------------------------------------------------------


def _encoded(text: str) -> str:
    """jsonify は非ASCIIを \\uXXXX へエスケープするので、その形で照合する。"""
    return json.dumps(text)[1:-1]


def test_draw_progress_is_empty_before_any_draw(client, db):
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)

    draw = client.get(f"/api/projects/{pid}/progress").get_json()["draw"]
    assert draw["subject_order_mode"] == "RANDOM_DRAW"
    assert draw["draw_cursor"] == 0
    assert draw["drawn"] == []
    assert draw["remaining_count"] == 4
    assert draw["can_draw"] is True


def test_draw_progress_is_inert_for_manual(client, db):
    created = _create(client, mode="MANUAL")
    pid = created["project_id"]
    start_scoring(client, pid)

    draw = client.get(f"/api/projects/{pid}/progress").get_json()["draw"]
    assert draw["subject_order_mode"] == "MANUAL"
    assert draw["drawn"] == []
    assert draw["can_draw"] is False


def test_draw_order_never_appears_in_public_payloads(client, db):
    """★ 秘密なのは順序。draw_order は field 名も値も出さない。"""
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)

    for path in (
        f"/api/projects/{pid}",
        f"/api/projects/{pid}/progress",
        f"/api/projects/{pid}/presentation-state",
    ):
        raw = client.get(path).get_data(as_text=True)
        assert "draw_order" not in raw, path
        assert "future_order" not in raw, path
        assert "next_subject" not in raw, path

    for path in (f"/host/{pid}", f"/host/{pid}/settings"):
        html = client.get(path).get_data(as_text=True)
        assert "draw_order" not in html, path


def test_subject_names_are_allowed_in_public_payloads(client, db):
    """★ Subject名そのものは秘密ではない(HostはSettingsで最初から知っている)。"""
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)

    detail = client.get(f"/api/projects/{pid}").get_json()
    assert sorted(s["name"] for s in detail["subjects"]) == ["A", "B", "C", "D"]

    progress = client.get(f"/api/projects/{pid}/progress").get_json()
    assert sorted(s["name"] for s in progress["subjects"]) == ["A", "B", "C", "D"]


def test_public_subject_arrays_follow_sort_order_not_draw_order(client, db):
    """★ 配列の並び順自体が漏洩経路にならないこと。"""
    created = _create(client)
    pid = created["project_id"]
    start_scoring(client, pid)

    display = [
        s.name
        for s in Subject.query.filter_by(project_id=pid).order_by(Subject.sort_order).all()
    ]
    secret = [
        s.name
        for s in Subject.query.filter_by(project_id=pid).order_by(Subject.draw_order).all()
    ]

    for path in (
        f"/api/projects/{pid}",
        f"/api/projects/{pid}/progress",
        f"/api/projects/{pid}/presentation-state",
    ):
        names = [s["name"] for s in client.get(path).get_json()["subjects"]]
        assert names == display, path
        if secret != display:
            assert names != secret, f"{path}: 配列順が秘密順と一致している"


def test_order_mode_note_explains_what_the_order_is_used_for(client, db):
    """★ 手動で並べたのに使われない、という誤解を防ぐ説明があること。"""
    js = (APP_DIR / "static" / "js" / "host_settings.js").read_text(encoding="utf-8")
    body = js[js.index("const ORDER_MODE_NOTES = {"):]
    body = body[: body.index("\n  };")]

    assert "この並び順が採点・発表順として使用されます" in body
    assert "管理画面上の表示順" in body
    assert "抽選するまで表示されません" in body

    html = client.get(
        f"/host/{_create(client)['project_id']}/settings"
    ).get_data(as_text=True)
    assert 'id="subjectOrderModeField"' in html
    assert 'id="subjectOrderNote"' in html
    assert 'value="RANDOM_DRAW"' in html


# ---------------------------------------------------------------------------
# Phase 10B-5: 抽選 API(expected_cursor による compare-and-swap)
# ---------------------------------------------------------------------------


def _draw(client, project_id, expected_cursor):
    return client.post(
        f"/api/projects/{project_id}/draw-next-subject",
        json={"expected_cursor": expected_cursor},
    )


def _secret_order(project_id):
    return [
        s.name
        for s in Subject.query.filter_by(project_id=project_id)
        .order_by(Subject.draw_order)
        .all()
    ]


def _started(client, db, **overrides):
    created = _create(client, **overrides)
    pid = created["project_id"]
    start_scoring(client, pid)
    return created, pid, _secret_order(pid)


def test_first_draw_reveals_exactly_one_subject(client, db):
    _, pid, secret = _started(client, db)

    body = _draw(client, pid, 0).get_json()
    assert body["subject"]["name"] == secret[0]
    assert body["position"] == 1
    assert body["draw_cursor"] == 1
    assert body["remaining_count"] == 3
    assert body["replayed"] is False
    # 今回の1件しか入っていない
    assert set(body) == {"subject", "position", "draw_cursor", "remaining_count", "replayed"}


def test_successive_draws_follow_the_secret_order(client, db):
    _, pid, secret = _started(client, db)
    revealed = []
    for cursor in range(4):
        body = _draw(client, pid, cursor).get_json()
        revealed.append(body["subject"]["name"])
        assert body["position"] == cursor + 1
    assert revealed == secret
    assert _cursor(db, pid) == 4


# --- 冪等性 ---------------------------------------------------------------


def test_duplicate_retry_replays_without_consuming_the_next(client, db):
    """★ response が失われた後の再送で2組消費しないこと。"""
    _, pid, secret = _started(client, db)
    first = _draw(client, pid, 0).get_json()

    retry = _draw(client, pid, 0)
    assert retry.status_code == 200
    body = retry.get_json()
    assert body["subject"] == first["subject"]
    assert body["position"] == 1
    assert body["replayed"] is True
    assert _cursor(db, pid) == 1, "cursorが進んでいない"

    # 次の意図的な抽選は2組目
    nxt = _draw(client, pid, 1).get_json()
    assert nxt["subject"]["name"] == secret[1]
    assert nxt["replayed"] is False


def test_delayed_retry_of_an_old_cursor_still_replays(client, db):
    _, pid, secret = _started(client, db)
    for cursor in range(3):
        _draw(client, pid, cursor)

    body = _draw(client, pid, 0).get_json()
    assert body["subject"]["name"] == secret[0]
    assert body["replayed"] is True
    assert _cursor(db, pid) == 3


def test_multi_tab_same_cursor_yields_the_same_subject(client, db):
    """★ 同じ expected_cursor の同時POSTで、両方が同じ組を認識する。"""
    _, pid, secret = _started(client, db)

    first = _draw(client, pid, 0).get_json()
    second = _draw(client, pid, 0).get_json()

    assert first["subject"] == second["subject"] == {
        "id": first["subject"]["id"], "name": secret[0]
    }
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert _cursor(db, pid) == 1


def test_future_cursor_is_rejected_without_revealing_anything(client, db):
    _, pid, _secret = _started(client, db)
    for bad in (1, 2, 99):
        resp = _draw(client, pid, bad)
        assert resp.status_code == 409, bad
        assert "subject" not in resp.get_json()
    assert _cursor(db, pid) == 0


def test_exhausted_draw_is_rejected(client, db):
    _, pid, _secret = _started(client, db)
    for cursor in range(4):
        _draw(client, pid, cursor)

    resp = _draw(client, pid, 4)
    assert resp.status_code == 409
    assert "All subjects have been drawn" in resp.get_json()["error"]
    assert _cursor(db, pid) == 4


def test_expected_cursor_rejects_non_integers(client, db):
    _, pid, _secret = _started(client, db)
    for bad in ("0", 1.5, True, None, [], {}, -1):
        resp = _draw(client, pid, bad)
        assert resp.status_code == 400, repr(bad)
    assert _cursor(db, pid) == 0


# --- 状態・認可 -----------------------------------------------------------


def test_draw_is_rejected_for_manual_projects(client, db):
    created = _create(client, mode="MANUAL")
    pid = created["project_id"]
    start_scoring(client, pid)
    resp = _draw(client, pid, 0)
    assert resp.status_code == 409
    assert "does not use a random draw" in resp.get_json()["error"]


def test_draw_is_rejected_outside_scoring(client, app, db):
    created = _create(client)
    pid = created["project_id"]
    assert _draw(client, pid, 0).status_code == 409  # DRAFT

    start_scoring(client, pid)
    for info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, info["code"])
        for subject in subjects_for(pid):
            score_and_submit(scorer, pid, subject.id)
    for cursor in range(4):
        _draw(client, pid, cursor)
    client.post(f"/api/projects/{pid}/transition", json={"target_status": "LOCKED"})

    resp = _draw(client, pid, 4)
    assert resp.status_code == 409


def test_draw_requires_a_host_session(client, app, db):
    _, pid, _secret = _started(client, db)
    anonymous = app.test_client()
    resp = anonymous.post(
        f"/api/projects/{pid}/draw-next-subject", json={"expected_cursor": 0}
    )
    assert resp.status_code == 403
    assert _cursor(db, pid) == 0


def test_draw_rejects_another_projects_host(client, app, db):
    other = app.test_client()
    theirs = _create(other, name="他人の")
    start_scoring(other, theirs["project_id"])
    _create(client)

    resp = _draw(client, theirs["project_id"], 0)
    assert resp.status_code == 403
    assert _cursor(db, theirs["project_id"]) == 0


def test_draw_never_leaks_future_order(client, db):
    """★ レスポンスに今回の1件以外が含まれないこと。"""
    _, pid, secret = _started(client, db)
    raw = _draw(client, pid, 0).get_data(as_text=True)

    assert _encoded(secret[0]) in raw
    for future in secret[1:]:
        assert _encoded(future) not in raw, future
    assert "draw_order" not in raw


# --- SEQUENTIAL の duplicate retry(§1 の最重要ケース) ---------------------


def test_sequential_draw_starts_scoring_for_that_subject(client, db):
    _, pid, secret = _started(client, db, presentation_mode="SEQUENTIAL", scorers=["s1"])

    body = _draw(client, pid, 0).get_json()
    statuses = {s.name: s.presentation_status for s in subjects_for(pid)}
    assert statuses[body["subject"]["name"]] == "SCORING"
    assert sorted(statuses.values()) == ["SCORING", "WAITING", "WAITING", "WAITING"]


def test_sequential_duplicate_retry_replays_even_while_scoring(client, db):
    """★★ 最重要。初回成功 -> response消失 -> 同じcursorで再送。

    この時点で今回Subjectは既にSCORING。「発表中なら409」を先に判定すると
    本来のduplicate retryが弾かれてしまうので、cursorの比較を先に行う。
    """
    _, pid, secret = _started(client, db, presentation_mode="SEQUENTIAL", scorers=["s1"])

    first = _draw(client, pid, 0).get_json()
    in_progress = [s for s in subjects_for(pid) if s.presentation_status == "SCORING"]
    assert len(in_progress) == 1, "現在SCORINGのSubjectが居る状態"

    retry = _draw(client, pid, 0)
    assert retry.status_code == 200, "duplicate retry が409になってはいけない"
    body = retry.get_json()
    assert body["subject"] == first["subject"]
    assert body["replayed"] is True
    assert _cursor(db, pid) == 1, "cursorが進んでいない"
    # 状態も変わっていない
    assert [s.id for s in subjects_for(pid) if s.presentation_status == "SCORING"] == [
        in_progress[0].id
    ]


def test_sequential_blocks_a_new_draw_while_a_subject_is_in_progress(client, db):
    """新規drawだけは発表中に拒否する(retryとは区別する)。"""
    _, pid, _secret = _started(client, db, presentation_mode="SEQUENTIAL", scorers=["s1"])
    _draw(client, pid, 0)

    resp = _draw(client, pid, 1)
    assert resp.status_code == 409
    assert "Finish the current subject" in resp.get_json()["error"]
    assert _cursor(db, pid) == 1


def test_sequential_full_cycle(client, app, db):
    """抽選 -> 採点 -> 締切 -> 発表 -> 確定 -> 次の抽選 の一巡。"""
    created, pid, secret = _started(
        client, db, presentation_mode="SEQUENTIAL", scorers=["s1"], subjects=["A", "B"]
    )
    secret = _secret_order(pid)

    for cursor, expected_name in enumerate(secret):
        body = _draw(client, pid, cursor).get_json()
        assert body["subject"]["name"] == expected_name

        subject = [s for s in subjects_for(pid) if s.name == expected_name][0]
        scorer = app.test_client()
        login_scorer(scorer, created["scorers"][0]["code"])
        score_and_submit(scorer, pid, subject.id)

        assert client.post(
            f"/api/projects/{pid}/subjects/{subject.id}/lock"
        ).status_code == 200
        assert client.post(
            f"/api/projects/{pid}/subjects/{subject.id}/present"
        ).status_code == 200

        # 次は自動でSCORINGにならない(抽選するまで待つ)
        remaining = [s for s in subjects_for(pid) if s.presentation_status == "SCORING"]
        assert remaining == []

    assert _cursor(db, pid) == 2
    assert all(s.presentation_status == "PRESENTED" for s in subjects_for(pid))


def test_sequential_manual_still_auto_advances(client, app, db):
    """MANUAL の自動前進が無回帰であること。"""
    created = _create(client, mode="MANUAL", presentation_mode="SEQUENTIAL",
                      scorers=["s1"], subjects=["A", "B"])
    pid = created["project_id"]
    start_scoring(client, pid)

    first = [s for s in subjects_for(pid) if s.presentation_status == "SCORING"][0]
    scorer = app.test_client()
    login_scorer(scorer, created["scorers"][0]["code"])
    score_and_submit(scorer, pid, first.id)
    client.post(f"/api/projects/{pid}/subjects/{first.id}/lock")
    client.post(f"/api/projects/{pid}/subjects/{first.id}/present")

    nxt = [s for s in subjects_for(pid) if s.presentation_status == "SCORING"]
    assert len(nxt) == 1 and nxt[0].name == "B"


# --- 履歴 / resume --------------------------------------------------------


def test_draw_progress_shows_history_but_never_the_future(client, db):
    """★ reload しても履歴と残数が復元でき、未来は含まれない。"""
    _, pid, secret = _started(client, db)
    _draw(client, pid, 0)
    _draw(client, pid, 1)

    progress = client.get(f"/api/projects/{pid}/progress").get_json()
    draw = progress["draw"]
    assert [d["name"] for d in draw["drawn"]] == secret[:2]
    assert [d["position"] for d in draw["drawn"]] == [1, 2]
    assert draw["draw_cursor"] == 2
    assert draw["remaining_count"] == 2
    assert draw["can_draw"] is True

    raw = client.get(f"/api/projects/{pid}/progress").get_data(as_text=True)
    assert "draw_order" not in raw
    # 残り2組の「順番」は分からない(drawn にしか順位が付いていない)
    positions = {d["name"]: d["position"] for d in draw["drawn"]}
    assert set(positions) == set(secret[:2])


def test_resume_continues_from_the_stored_cursor(client, db):
    _, pid, secret = _started(client, db)
    _draw(client, pid, 0)

    # 「reload」= 状態を読み直して、返ってきた cursor をそのまま次に使う
    cursor = client.get(f"/api/projects/{pid}/progress").get_json()["draw"]["draw_cursor"]
    body = _draw(client, pid, cursor).get_json()
    assert body["subject"]["name"] == secret[1]
    assert body["position"] == 2


# --- 実装契約 -------------------------------------------------------------


def test_draw_checks_the_cursor_before_the_in_progress_guard():
    """★ 判定順: cursor 比較 -> (Case C でのみ) 発表中チェック -> CAS。"""
    source = (APP_DIR / "services" / "project_service.py").read_text(encoding="utf-8")
    body = source[source.index("def draw_next_subject("):]
    body = body[: body.index("\ndef _draw_result(")]

    replay = body.index("if expected_cursor < project.draw_cursor:")
    future = body.index("if expected_cursor > project.draw_cursor:")
    in_progress = body.index('presentation_status="SCORING"')
    cas = body.index("Project.draw_cursor == expected_cursor")

    assert replay < future < in_progress < cas


def test_draw_uses_a_single_compare_and_swap():
    source = (APP_DIR / "services" / "project_service.py").read_text(encoding="utf-8")
    body = source[source.index("def draw_next_subject("):]
    body = body[: body.index("\ndef _draw_result(")]
    assert "Project.draw_cursor == expected_cursor" in body
    assert "Project.draw_cursor + 1" in body
    assert "updated != 1" in body


# --- Host Dashboard は履歴表示のみ ------------------------------------------


def test_dashboard_shows_draw_history_only(client, db):
    """★ 抽選の実行入口は Host Dashboard に置かない。"""
    js = (APP_DIR / "static" / "js" / "host_dashboard.js").read_text(encoding="utf-8")
    body = js[js.index("function renderDraw(data) {"):]
    body = body[: body.index("\n  }")]

    assert "draw.drawn" in body
    assert "remaining_count" in body
    assert "？？？" in body
    # 抽選は実行しない
    assert "draw-next-subject" not in body
    assert "expected_cursor" not in body
    assert "apiFetch" not in body
    # 抽選画面への導線だけを出す
    assert "/draw`" in body


def test_dashboard_warns_before_every_draw_is_done(client, db):
    js = (APP_DIR / "static" / "js" / "host_dashboard.js").read_text(encoding="utf-8")
    assert "全被採点者の発表順抽選を完了してから採点を締め切ってください" in js
    body = js[js.index("function renderDraw(data) {"):]
    body = body[: body.index("\n  }")]
    assert "closeButton" in body and "disabled = incomplete" in body


def test_dashboard_draw_section_is_hidden_for_manual(client, db):
    created = _create(client, mode="MANUAL")
    html = client.get(f"/host/{created['project_id']}").get_data(as_text=True)
    tag = re.search(r'<section[^>]*id="drawSection"[^>]*>', html)
    assert tag and "hidden" in tag.group(0)
