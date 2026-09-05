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
