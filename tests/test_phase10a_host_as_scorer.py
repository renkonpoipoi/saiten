"""Phase 10A: Host-as-Scorer の概念モデル是正。

Host role は Scorer の属性であって別人格ではない。「ホスト自身も採点する」を
ON にしても採点者は増えず、入力済み Scorer のうち 1 人に is_host_scorer が立つ。

Phase 10A 以前は「ホスト」という名前の合成 Scorer を 1 人追加していた。
そのため Scorer 数が +1 され、参加者コード一括コピーにもホスト本人のコードが
混ざっていた。ここではその是正を固定する。

is_host_scorer は従来どおり表示・割り当て用のフラグであり、権限判定には
一切使わない(app/auth/decorators.py を参照)。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.models import Evaluation, Project, Scorer, Subject
from tests.helpers import create_project, start_scoring, subjects_for

APP_DIR = Path(__file__).resolve().parent.parent / "app"
JS_DIR = APP_DIR / "static" / "js"

FOUR_SCORERS = ["吉田", "田中", "佐藤", "山田"]


def _scorers(project_id: int) -> list[Scorer]:
    return Scorer.query.filter_by(project_id=project_id).order_by(Scorer.id).all()


def _host_scorers(project_id: int) -> list[Scorer]:
    return [s for s in _scorers(project_id) if s.is_host_scorer]


def _create(client, **overrides):
    payload = {"scorers": list(FOUR_SCORERS)}
    payload.update(overrides)
    return create_project(client, **payload)


# ---------------------------------------------------------------------------
# host scoring OFF: 既存挙動を完全に維持する
# ---------------------------------------------------------------------------


def test_host_scoring_off_keeps_every_scorer_as_a_plain_participant(client, db):
    created = _create(client, allow_host_scoring=False)
    rows = _scorers(created["project_id"])

    assert [s.display_name for s in rows] == FOUR_SCORERS
    assert len(rows) == len(FOUR_SCORERS)
    assert all(not s.is_host_scorer for s in rows)
    assert db.session.get(Project, created["project_id"]).allow_host_scoring is False


def test_host_scoring_off_ignores_a_stray_host_scorer_index(client, db):
    """OFF のときは index を送られても無視する(Host 兼任を勝手に作らない)。"""
    created = _create(client, allow_host_scoring=False, host_scorer_index=2)
    assert _host_scorers(created["project_id"]) == []


def test_host_scoring_off_response_marks_nobody_as_host(client, db):
    created = _create(client, allow_host_scoring=False)
    assert all(s["is_host_scorer"] is False for s in created["scorers"])


# ---------------------------------------------------------------------------
# host scoring ON: Scorer 数を増やさず、既存 Scorer に role を割り当てる
# ---------------------------------------------------------------------------


def test_host_scoring_on_does_not_add_an_extra_scorer(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    rows = _scorers(created["project_id"])

    # ★ Phase 10A の中心。入力した人数のまま。
    assert len(rows) == len(FOUR_SCORERS)
    assert [s.display_name for s in rows] == FOUR_SCORERS
    assert len(created["scorers"]) == len(FOUR_SCORERS)


def test_host_scoring_on_never_creates_a_synthetic_host_scorer(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    names = [s.display_name for s in _scorers(created["project_id"])]
    assert "ホスト" not in names
    # 合成Scorer名の定数そのものが廃止されていること
    source = (
        Path(__file__).resolve().parent.parent
        / "app" / "services" / "project_service.py"
    ).read_text(encoding="utf-8")
    assert "HOST_SCORER_DISPLAY_NAME" not in source


def test_host_scoring_on_flags_exactly_the_selected_scorer(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=2)
    rows = _scorers(created["project_id"])

    hosts = [s for s in rows if s.is_host_scorer]
    assert len(hosts) == 1
    assert hosts[0].display_name == "佐藤"
    assert [s.is_host_scorer for s in rows] == [False, False, True, False]


def test_host_scoring_on_can_select_the_first_and_last_scorer(client, db):
    first = _create(client, allow_host_scoring=True, host_scorer_index=0)
    assert _host_scorers(first["project_id"])[0].display_name == "吉田"

    other = _create(client, name="別", allow_host_scoring=True, host_scorer_index=3)
    assert _host_scorers(other["project_id"])[0].display_name == "山田"


def test_host_scorer_still_gets_a_normal_scorer_code(client, db):
    """Host 兼任でも Scorer Code は通常どおり発行・hash 保存する。

    access_code_hash は nullable=False + UNIQUE で、発行しない設計は
    スキーマ変更を伴う。Host 本人が Code を使わなくても、復旧経路として残す。
    """
    from app.services.code_service import hash_code

    created = _create(client, allow_host_scoring=True, host_scorer_index=1)
    host_payload = [s for s in created["scorers"] if s["is_host_scorer"]][0]

    assert host_payload["display_name"] == "田中"
    assert host_payload["code"].startswith("scr_")

    stored = {s.access_code_hash for s in _scorers(created["project_id"])}
    assert hash_code(host_payload["code"]) in stored
    # 平文は保存しない
    assert host_payload["code"] not in stored


def test_host_code_and_host_scorer_code_stay_separate(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project = db.session.get(Project, created["project_id"])
    scorer_hashes = {s.access_code_hash for s in _scorers(project.id)}

    assert project.host_code_hash not in scorer_hashes
    assert created["host_code"].startswith("host_")
    assert all(s["code"] != created["host_code"] for s in created["scorers"])


# ---------------------------------------------------------------------------
# host_scorer_index の検証
# ---------------------------------------------------------------------------


def test_host_scorer_index_is_required_when_host_scoring_is_on(client, db):
    resp = client.post(
        "/api/projects",
        json={
            "name": "x",
            "subjects": ["A"],
            "scorers": FOUR_SCORERS,
            "criteria": ["a", "b", "c", "d", "e"],
            "allow_host_scoring": True,
        },
    )
    assert resp.status_code == 400
    assert "host_scorer_index" in resp.get_json()["error"]


def test_host_scorer_index_rejects_out_of_range(client, db):
    for bad in (-1, len(FOUR_SCORERS), 99):
        resp = client.post(
            "/api/projects",
            json={
                "name": "x",
                "subjects": ["A"],
                "scorers": FOUR_SCORERS,
                "criteria": ["a", "b", "c", "d", "e"],
                "allow_host_scoring": True,
                "host_scorer_index": bad,
            },
        )
        assert resp.status_code == 400, bad


def test_host_scorer_index_rejects_non_integer_values(client, db):
    # bool は int のサブクラスなので、True が 1 として通らないことも確認する
    for bad in ("0", 1.5, True, False, [], {}, "吉田"):
        resp = client.post(
            "/api/projects",
            json={
                "name": "x",
                "subjects": ["A"],
                "scorers": FOUR_SCORERS,
                "criteria": ["a", "b", "c", "d", "e"],
                "allow_host_scoring": True,
                "host_scorer_index": bad,
            },
        )
        assert resp.status_code == 400, repr(bad)


def test_rejected_creation_leaves_no_project_behind(client, db):
    before = Project.query.count()
    client.post(
        "/api/projects",
        json={
            "name": "x",
            "subjects": ["A"],
            "scorers": FOUR_SCORERS,
            "criteria": ["a", "b", "c", "d", "e"],
            "allow_host_scoring": True,
            "host_scorer_index": 99,
        },
    )
    assert Project.query.count() == before


def test_host_scorer_index_counts_the_cleaned_scorer_array(client, db):
    """index は「空文字除去後の配列」基準(client と server で同じ基準)。"""
    created = create_project(
        client,
        scorers=["吉田", "   ", "田中", "", "佐藤"],
        allow_host_scoring=True,
        host_scorer_index=1,
    )
    rows = _scorers(created["project_id"])
    assert [s.display_name for s in rows] == ["吉田", "田中", "佐藤"]
    assert _host_scorers(created["project_id"])[0].display_name == "田中"


# ---------------------------------------------------------------------------
# 下流(Evaluation / Presentation)が実人数に一致すること
# ---------------------------------------------------------------------------


def test_evaluations_match_the_entered_scorer_count(client, db):
    created = _create(client, subjects=["A", "B", "C"],
                      allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    scorer_count = len(_scorers(project_id))
    subject_count = Subject.query.filter_by(project_id=project_id).count()
    assert scorer_count == len(FOUR_SCORERS)
    assert subject_count == 3
    assert (
        Evaluation.query.filter_by(project_id=project_id).count()
        == scorer_count * subject_count
    )


def test_presentation_judge_count_matches_the_entered_scorers(client, app, db):
    """Phase 9 の Judge rail に「ホスト」という余分な Judge が出ないこと。"""
    from tests.helpers import login_scorer, score_and_submit

    created = _create(client, subjects=["A", "B"],
                      allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    for scorer_info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        for subject in subjects_for(project_id):
            score_and_submit(scorer, project_id, subject.id)

    for target in ("LOCKED", "PRESENTING"):
        assert (
            client.post(
                f"/api/projects/{project_id}/transition", json={"target_status": target}
            ).status_code
            == 200
        )

    summary = client.get(f"/api/projects/{project_id}/result-summary").get_json()
    for subject in summary["subjects"]:
        judges = [j["display_name"] for j in subject["judge_totals"]]
        assert len(judges) == len(FOUR_SCORERS)
        assert judges == FOUR_SCORERS
        assert "ホスト" not in judges
    assert summary["official_scorer_count"] == len(FOUR_SCORERS)


def test_host_scoring_does_not_change_official_scorer_logic(client, app, db):
    """is_host_scorer は集計対象の判定に一切影響しない。"""
    from app.services.project_service import official_scorer_ids

    created = _create(client, subjects=["A"], allow_host_scoring=True, host_scorer_index=1)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    project = db.session.get(Project, project_id)
    # まだ誰も提出していないのでBATCHのeligibleは空(host兼任も例外扱いされない)
    assert official_scorer_ids(project) == set()


# ---------------------------------------------------------------------------
# Phase 10A-2: 参加者向け一括コピーの対象
# ---------------------------------------------------------------------------


def _create_js() -> str:
    return (JS_DIR / "project_create.js").read_text(encoding="utf-8")


def test_bulk_copy_excludes_only_the_host_scorer():
    js = _create_js()
    build = js[js.index("function buildScorerCodeText("):]
    build = build[: build.index("\n  }")]
    assert "!scorer.is_host_scorer" in build
    # ホスト兼任「以外」は全員含める(参加者を取りこぼさない)
    assert "scorer.display_name" in build
    assert "scorer.code" in build


def test_bulk_copy_still_never_touches_the_host_code():
    """Phase 8D からの不変条件: ホストコードは一括コピーに入らない。"""
    js = _create_js()
    build = js[js.index("function buildScorerCodeText("):]
    build = build[: build.index("\n  }")]
    assert "host_code" not in build
    assert "hostCodeText" not in build


def test_bulk_copy_note_names_the_host_scorer_when_there_is_one():
    js = _create_js()
    note = js[js.index("function buildBulkCopyNote("):]
    note = note[: note.index("\n  }")]
    assert "ホストコードと、ホスト兼任の採点者" in note
    assert "host.display_name" in note
    # ホスト兼任がいない場合は不自然にならないよう分岐する
    assert "if (!host)" in note
    assert "ホストコードは含まれません" in note


def test_bulk_copy_sends_nothing_back_to_the_server():
    """Phase 8D からの不変条件: 作成レスポンスを使うだけで再通信しない。"""
    js = _create_js()
    assert js.count("apiFetch(") == 1
    assert '"/api/projects"' in js


def test_host_scorer_row_is_still_listed_with_its_own_copy_button():
    """兼任者の行は残す(誰が兼任かが見えることに意味がある)。"""
    js = _create_js()
    assert '"(ホスト兼任)"' in js
    assert 'tr.dataset.hostScorer = "true"' in js
    assert '"配布不要"' in js


def test_created_page_marks_the_host_scorer(client, db):
    """作成完了画面がホスト兼任であることを示せる形になっていること。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    flags = {s["display_name"]: s["is_host_scorer"] for s in created["scorers"]}
    assert flags == {"吉田": True, "田中": False, "佐藤": False, "山田": False}


# ---------------------------------------------------------------------------
# Phase 10A-3: DRAFT中のHost兼任の付け替え
# ---------------------------------------------------------------------------


def _patch_host_scorer(client, project_id, scorer_id):
    return client.patch(
        f"/api/projects/{project_id}/host-scorer", json={"scorer_id": scorer_id}
    )


def test_host_scorer_can_be_assigned_while_draft(client, db):
    created = _create(client, allow_host_scoring=False)
    project_id = created["project_id"]
    target = _scorers(project_id)[2]

    resp = _patch_host_scorer(client, project_id, target.id)
    assert resp.status_code == 200

    hosts = _host_scorers(project_id)
    assert [s.id for s in hosts] == [target.id]
    assert db.session.get(Project, project_id).allow_host_scoring is True


def test_host_scorer_reassignment_moves_the_flag_only(client, db):
    """A -> B の付け替えで、途中も含めて2人trueにならず、人数も変わらない。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    rows = _scorers(project_id)
    a, b = rows[0], rows[3]
    assert a.is_host_scorer and not b.is_host_scorer

    assert _patch_host_scorer(client, project_id, b.id).status_code == 200

    after = _scorers(project_id)
    assert len(after) == len(FOUR_SCORERS)
    assert [s.is_host_scorer for s in after] == [False, False, False, True]
    assert len(_host_scorers(project_id)) == 1


def test_host_scorer_reassignment_backwards_also_works(client, db):
    """新Hostのidが旧Hostより小さい場合(=flush順が逆転しうる)も成功する。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=3)
    project_id = created["project_id"]
    rows = _scorers(project_id)
    assert rows[3].is_host_scorer

    assert _patch_host_scorer(client, project_id, rows[0].id).status_code == 200
    assert [s.is_host_scorer for s in _scorers(project_id)] == [True, False, False, False]


def test_setting_the_same_host_scorer_is_idempotent(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=1)
    project_id = created["project_id"]
    target = _scorers(project_id)[1]

    for _ in range(3):
        assert _patch_host_scorer(client, project_id, target.id).status_code == 200
    assert [s.id for s in _host_scorers(project_id)] == [target.id]


def test_host_scorer_can_be_cleared(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]

    resp = _patch_host_scorer(client, project_id, None)
    assert resp.status_code == 200
    assert _host_scorers(project_id) == []
    assert db.session.get(Project, project_id).allow_host_scoring is False
    # 採点者は消えていない
    assert len(_scorers(project_id)) == len(FOUR_SCORERS)


def test_host_scorer_patch_rejects_another_projects_scorer(client, app, db):
    mine = _create(client, allow_host_scoring=False)
    other_client = app.test_client()
    theirs = _create(other_client, name="他人の", allow_host_scoring=False)
    their_scorer = _scorers(theirs["project_id"])[0]

    resp = _patch_host_scorer(client, mine["project_id"], their_scorer.id)
    assert resp.status_code == 403
    assert _host_scorers(mine["project_id"]) == []
    assert _host_scorers(theirs["project_id"]) == []


def test_host_scorer_patch_requires_a_host_session(client, app, db):
    created = _create(client, allow_host_scoring=False)
    anonymous = app.test_client()
    resp = anonymous.patch(
        f"/api/projects/{created['project_id']}/host-scorer",
        json={"scorer_id": _scorers(created["project_id"])[0].id},
    )
    assert resp.status_code == 403


def test_host_scorer_patch_rejects_non_integer_ids(client, db):
    created = _create(client, allow_host_scoring=False)
    project_id = created["project_id"]
    for bad in ("1", 1.5, True, [], {}):
        resp = _patch_host_scorer(client, project_id, bad)
        assert resp.status_code == 400, repr(bad)


def test_host_scorer_cannot_be_changed_after_scoring_starts(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    target = _scorers(project_id)[1]
    resp = _patch_host_scorer(client, project_id, target.id)
    assert resp.status_code == 409
    # 変わっていない
    assert [s.id for s in _host_scorers(project_id)] == [_scorers(project_id)[0].id]


def test_deleting_the_host_scorer_clears_allow_host_scoring(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=2)
    project_id = created["project_id"]
    host = _host_scorers(project_id)[0]

    resp = client.delete(f"/api/projects/{project_id}/scorers/{host.id}")
    assert resp.status_code == 204
    assert _host_scorers(project_id) == []
    assert db.session.get(Project, project_id).allow_host_scoring is False
    assert len(_scorers(project_id)) == len(FOUR_SCORERS) - 1


def test_deleting_a_plain_scorer_keeps_the_host_assignment(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    host = _host_scorers(project_id)[0]
    victim = _scorers(project_id)[2]

    assert client.delete(
        f"/api/projects/{project_id}/scorers/{victim.id}"
    ).status_code == 204
    assert [s.id for s in _host_scorers(project_id)] == [host.id]
    assert db.session.get(Project, project_id).allow_host_scoring is True


# ---------------------------------------------------------------------------
# Legacy DRAFT Project (旧方式で作られた合成「ホスト」Scorer) の非破壊性
# ---------------------------------------------------------------------------


def _legacy_draft_project(client, db):
    """旧方式で作られたDRAFT Projectを再現する。

    「吉田 / 田中 / 佐藤 / ホスト(is_host_scorer=True)」という構成。
    Phase 10A では自動変換も自動削除も行わない。
    """
    from app.services.code_service import generate_scorer_code, hash_code

    created = create_project(
        client, scorers=["吉田", "田中", "佐藤"], allow_host_scoring=False
    )
    project = db.session.get(Project, created["project_id"])
    legacy = Scorer(
        project_id=project.id,
        display_name="ホスト",
        access_code_hash=hash_code(generate_scorer_code()),
        is_host_scorer=True,
    )
    project.allow_host_scoring = True
    db.session.add(legacy)
    db.session.commit()
    return project, legacy


def test_legacy_host_scorer_is_kept_when_the_role_moves(client, db):
    """★ display_name == "ホスト" だけを根拠に旧Scorerを自動削除しない。

    「ホスト」という名前の正規のScorerである可能性を否定できないため、
    Host roleの付け替えはフラグの移動だけに留める。
    """
    project, legacy = _legacy_draft_project(client, db)
    new_host = _scorers(project.id)[0]
    assert new_host.display_name == "吉田"

    assert _patch_host_scorer(client, project.id, new_host.id).status_code == 200

    names = [s.display_name for s in _scorers(project.id)]
    # 合成Scorerは残り続ける(削除は明示操作でのみ行う)
    assert "ホスト" in names
    assert len(names) == 4
    assert db.session.get(Scorer, legacy.id) is not None
    assert db.session.get(Scorer, legacy.id).is_host_scorer is False
    assert [s.id for s in _host_scorers(project.id)] == [new_host.id]


def test_legacy_host_scorer_can_still_be_removed_explicitly(client, db):
    """自動cleanupはしないが、既存のDRAFT編集で明示的に消すことはできる。"""
    project, legacy = _legacy_draft_project(client, db)
    new_host = _scorers(project.id)[0]
    _patch_host_scorer(client, project.id, new_host.id)

    assert client.delete(
        f"/api/projects/{project.id}/scorers/{legacy.id}"
    ).status_code == 204
    assert "ホスト" not in [s.display_name for s in _scorers(project.id)]
    # ホスト兼任は吉田のまま(巻き添えで解除されない)
    assert [s.id for s in _host_scorers(project.id)] == [new_host.id]


def test_legacy_project_still_works_end_to_end(client, app, db):
    """旧方式のProjectをそのまま採点・集計まで通せること(非破壊)。"""
    from tests.helpers import login_scorer

    project, legacy = _legacy_draft_project(client, db)
    project_id = project.id
    assert len(_scorers(project_id)) == 4

    start_scoring(client, project_id)
    assert (
        Evaluation.query.filter_by(project_id=project_id).count()
        == 4 * Subject.query.filter_by(project_id=project_id).count()
    )
    # 旧「ホスト」Scorerも通常のScorerとしてログインできる
    code = project_service_regenerate(client, project_id, legacy.id)
    scorer = app.test_client()
    login_scorer(scorer, code)
    assert scorer.get("/api/scorer/me/evaluations").status_code == 200


def project_service_regenerate(client, project_id, scorer_id) -> str:
    resp = client.post(
        f"/api/projects/{project_id}/scorers/{scorer_id}/regenerate-code"
    )
    assert resp.status_code == 200
    return resp.get_json()["code"]


# ---------------------------------------------------------------------------
# 付け替え順序 (10A-5 の partial UNIQUE INDEX と共存できること)
# ---------------------------------------------------------------------------


def test_reassignment_demotes_before_promoting(client, db):
    """旧Hostを降ろすUPDATEを、新Hostを立てるUPDATEより先にDBへ送ること。

    SQLAlchemyのunit of workは同一flush内のUPDATEを主キー順に並べるため、
    明示的なflushが無いと「新Hostを立てる」方が先に飛び、一時的に2人trueに
    なって部分UNIQUE INDEXに違反しうる。
    """
    source = (
        Path(__file__).resolve().parent.parent
        / "app" / "services" / "project_service.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("def set_host_scorer("):]
    body = body[: body.index("\ndef ", 1)] if "\ndef " in body[1:] else body

    demote = body.index("existing.is_host_scorer = False")
    flush = body.index("db.session.flush()")
    promote = body.index("scorer.is_host_scorer = True")
    assert demote < flush < promote


def _create_host_uniqueness_index(db):
    """10A-5 で migration が作るのと同じ部分UNIQUE INDEXをテスト内で張る。

    静的なソース順の検査だけでは「実際にUNIQUE違反しないこと」を保証できない
    ため、実際に制約下で付け替えを走らせる。
    """
    db.session.execute(
        db.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_scorers_one_host_per_project"
            " ON scorers (project_id) WHERE is_host_scorer"
        )
    )
    db.session.commit()


def test_reassignment_succeeds_under_the_partial_unique_index(client, db):
    """★ UNIQUE INDEX 存在下で A -> B -> A と付け替えても成功すること。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    _create_host_uniqueness_index(db)

    rows = _scorers(project_id)
    a, b, c = rows[0], rows[3], rows[1]

    # 前へ(id小 -> id大)
    assert _patch_host_scorer(client, project_id, b.id).status_code == 200
    assert [s.id for s in _host_scorers(project_id)] == [b.id]

    # 後ろへ(id大 -> id小。flush順が逆転しうる向き)
    assert _patch_host_scorer(client, project_id, a.id).status_code == 200
    assert [s.id for s in _host_scorers(project_id)] == [a.id]

    # 3人目へ
    assert _patch_host_scorer(client, project_id, c.id).status_code == 200
    assert [s.id for s in _host_scorers(project_id)] == [c.id]

    # 解除して再設定
    assert _patch_host_scorer(client, project_id, None).status_code == 200
    assert _host_scorers(project_id) == []
    assert _patch_host_scorer(client, project_id, b.id).status_code == 200
    assert [s.id for s in _host_scorers(project_id)] == [b.id]


def test_index_actually_rejects_two_host_scorers(client, db):
    """検査そのものが有効であることの確認(索引が効いていないと無意味なため)。"""
    import sqlalchemy

    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    _create_host_uniqueness_index(db)

    rows = _scorers(project_id)
    try:
        db.session.execute(
            db.text("UPDATE scorers SET is_host_scorer = 1 WHERE id = :i"),
            {"i": rows[1].id},
        )
        db.session.commit()
        raised = False
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
        raised = True
    assert raised, "partial unique index が効いていない"


def test_creation_under_the_index_flags_only_one_scorer(client, db):
    """作成時も1人しか立てないので、索引がある状態でも作成できる。"""
    _create_host_uniqueness_index(db)
    created = _create(client, allow_host_scoring=True, host_scorer_index=2)
    assert len(_host_scorers(created["project_id"])) == 1

    # 索引はproject単位。別Projectでもそれぞれ1人ずつ立てられる。
    other = _create(client, name="別", allow_host_scoring=True, host_scorer_index=0)
    assert len(_host_scorers(other["project_id"])) == 1


# ---------------------------------------------------------------------------
# Phase 10A-4 / 10A-6: Host本人がコード入力なしで自分の採点画面へ入る
#
# 遷移は通常のHTML form (POST + target="_blank") から
# POST /host/<id>/scoring が行い、303 で /scorer へ redirect する。
# about:blank を開いて JS から WindowProxy.location を書き換える方式は
# Phase 10A-6 で廃止した(opener を切ると実ブラウザで遷移が通らない)。
# ---------------------------------------------------------------------------


def _open_host_scoring(client, project_id):
    return client.post(f"/host/{project_id}/scoring")


def test_host_scoring_entry_redirects_to_the_scorer_dashboard(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=2)
    project_id = created["project_id"]
    host = _host_scorers(project_id)[0]

    resp = _open_host_scoring(client, project_id)
    # 303: POSTの結果をGETで取りに行かせる(再読み込みでPOSTが再送されない)
    assert resp.status_code == 303
    assert resp.headers["Location"].endswith("/scorer")

    with client.session_transaction() as sess:
        assert sess["scorer_id"] == host.id
        assert sess["scorer_project_id"] == project_id


def test_host_scoring_entry_lands_on_a_usable_scorer_dashboard(client, db):
    """redirect先を実際にたどって、採点画面が表示できること。"""
    created = _create(client, subjects=["A"], allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    resp = client.post(f"/host/{project_id}/scoring", follow_redirects=True)
    assert resp.status_code == 200
    assert 'id="scoringPanel"' in resp.get_data(as_text=True)
    assert client.get("/api/scorer/me/evaluations").status_code == 200


def test_host_and_scorer_sessions_coexist(client, db):
    """★ 元タブのHost Dashboardと新タブのScorer Dashboardを同時に使える。"""
    created = _create(client, subjects=["A"], allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    assert _open_host_scoring(client, project_id).status_code == 303

    with client.session_transaction() as sess:
        # 別keyなので共存する。Host権限は破棄されない。
        assert sess["host_project_id"] == project_id
        assert sess["scorer_id"] == _host_scorers(project_id)[0].id

    # 両方のAPIが同時に使える
    assert client.get(f"/api/projects/{project_id}/progress").status_code == 200
    assert client.get("/api/scorer/me/evaluations").status_code == 200
    # 元タブのHost Dashboardもそのまま開ける
    assert client.get(f"/host/{project_id}").status_code == 200


def test_host_scoring_entry_never_takes_a_client_supplied_id(client, app, db):
    """clientがscorer_idを送っても無視する(なりすまし経路を作らない)。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    rows = _scorers(project_id)
    host, victim = rows[0], rows[2]

    resp = client.post(
        f"/host/{project_id}/scoring",
        data={"scorer_id": str(victim.id), "display_name": "佐藤"},
    )
    assert resp.status_code == 303
    with client.session_transaction() as sess:
        assert sess["scorer_id"] == host.id


def test_host_scoring_entry_is_post_only(client, db):
    """★ GETでsessionを書き換えない(プリフェッチ等で差し替わらない)。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]

    resp = client.get(f"/host/{project_id}/scoring")
    assert resp.status_code == 405
    with client.session_transaction() as sess:
        assert "scorer_id" not in sess


def test_host_scoring_entry_requires_a_host_session(client, app, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    anonymous = app.test_client()
    resp = anonymous.post(f"/host/{created['project_id']}/scoring")
    assert resp.status_code == 403
    with anonymous.session_transaction() as sess:
        assert "scorer_id" not in sess


def test_host_scoring_entry_rejects_another_projects_host(client, app, db):
    other_client = app.test_client()
    theirs = _create(other_client, name="他人の", allow_host_scoring=True, host_scorer_index=0)

    mine = _create(client, allow_host_scoring=True, host_scorer_index=0)
    resp = _open_host_scoring(client, theirs["project_id"])
    assert resp.status_code == 403
    with client.session_transaction() as sess:
        assert "scorer_id" not in sess
    assert mine["project_id"] != theirs["project_id"]


def test_host_scoring_entry_409s_without_a_host_scorer(client, db):
    created = _create(client, allow_host_scoring=False)
    resp = _open_host_scoring(client, created["project_id"])
    assert resp.status_code == 409
    with client.session_transaction() as sess:
        assert "scorer_id" not in sess


def test_host_scoring_entry_ignores_inactive_host_scorers(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    host = _host_scorers(project_id)[0]
    host.is_active = False
    db.session.commit()

    resp = _open_host_scoring(client, project_id)
    assert resp.status_code == 409
    with client.session_transaction() as sess:
        assert "scorer_id" not in sess


def test_host_scoring_entry_follows_a_reassignment(client, db):
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    rows = _scorers(project_id)

    _open_host_scoring(client, project_id)
    with client.session_transaction() as sess:
        assert sess["scorer_id"] == rows[0].id

    _patch_host_scorer(client, project_id, rows[3].id)
    _open_host_scoring(client, project_id)
    with client.session_transaction() as sess:
        assert sess["scorer_id"] == rows[3].id


def test_host_scoring_entry_never_grants_host_rights_to_a_scorer(client, app, db):
    """逆方向(Scorer -> Host)の経路は作らない。"""
    from tests.helpers import login_scorer

    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    plain = [s for s in created["scorers"] if not s["is_host_scorer"]][0]

    scorer = app.test_client()
    login_scorer(scorer, plain["code"])
    assert _open_host_scoring(scorer, project_id).status_code == 403
    assert scorer.get(f"/api/projects/{project_id}/progress").status_code == 403


def test_host_scoring_entry_puts_no_credentials_in_the_url_or_body(client, db):
    """URLにもレスポンスにも平文コードを載せない。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    project_id = created["project_id"]
    resp = _open_host_scoring(client, project_id)

    body = resp.get_data(as_text=True)
    location = resp.headers["Location"]
    assert created["host_code"] not in body and created["host_code"] not in location
    for scorer in created["scorers"]:
        assert scorer["code"] not in body
        assert scorer["code"] not in location
    # redirect先は素の /scorer で、識別子を含まない
    assert location.endswith("/scorer")


def test_host_scoring_entry_is_csrf_protected(client_with_security, app_with_security):
    """CSRF有効時、tokenの無いPOSTは拒否され、sessionも書き換わらない。"""
    with app_with_security.app_context():
        from app.extensions import db as _db
        from app.models import Project, Scorer
        from app.services.code_service import generate_scorer_code, hash_code

        project = Project(
            name="csrf", status="DRAFT",
            host_code_hash=hash_code("host_csrf"), allow_host_scoring=True,
        )
        _db.session.add(project)
        _db.session.flush()
        _db.session.add(
            Scorer(
                project_id=project.id, display_name="吉田",
                access_code_hash=hash_code(generate_scorer_code()),
                is_host_scorer=True,
            )
        )
        _db.session.commit()
        project_id = project.id

    with client_with_security.session_transaction() as sess:
        sess["host_project_id"] = project_id

    resp = client_with_security.post(f"/host/{project_id}/scoring")
    assert resp.status_code == 400
    with client_with_security.session_transaction() as sess:
        assert "scorer_id" not in sess


# --- client 側の実装契約 (静的検査) ---------------------------------------


def _dashboard_js() -> str:
    return (JS_DIR / "host_dashboard.js").read_text(encoding="utf-8")


def _strip_js_comments(source: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


def test_dashboard_uses_a_plain_form_post(client, db):
    """★ 遷移は通常のHTML formが行う(JSはWindowProxyを触らない)。"""
    created = _create(client, allow_host_scoring=True, host_scorer_index=0)
    html = client.get(f"/host/{created['project_id']}").get_data(as_text=True)

    form = re.search(r'<form id="openHostScoringForm".*?</form>', html, re.S)
    assert form, "openHostScoringForm が見つからない"
    body = form.group(0)
    assert 'method="post"' in body
    assert 'target="_blank"' in body
    assert f'action="/host/{created["project_id"]}/scoring"' in body
    assert 'name="csrf_token"' in body
    assert 'type="submit"' in body
    assert 'id="openHostScoringButton"' in body


def test_dashboard_no_longer_opens_a_blank_tab_from_js():
    """★ about:blank + WindowProxy 操作のロジックが残っていないこと。"""
    js = _dashboard_js()
    for removed in (
        "window.open",
        "about:blank",
        "tab.location",
        "tab.opener",
        "tab.close",
        "host-scorer-session",
    ):
        assert removed not in js, removed


def test_host_scorer_session_json_endpoint_is_gone():
    """経路を1本に絞る(旧JSON endpointは撤去)。"""
    source = (
        Path(__file__).resolve().parent.parent / "app" / "routes" / "api_projects.py"
    ).read_text(encoding="utf-8")
    assert "host-scorer-session" not in source

    from app import create_app

    app = create_app({"APP_ENV": "testing", "SECRET_KEY": "x"})
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert not any("host-scorer-session" in rule for rule in rules)


def test_dashboard_never_puts_codes_in_the_url_or_storage():
    """実コード(コメントを除く)に credential の保存・送信が無いこと。"""
    js = _strip_js_comments(_dashboard_js())
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "host_code" not in js
    assert "scorer_id" not in js, "clientからscorer_idを送らない"


def test_dashboard_form_only_shows_with_a_host_scorer(client, db):
    js = _dashboard_js()
    body = js[js.index("function renderHostScoringLink("):]
    body = body[: body.index("\n  }")]
    assert "scorer.is_host_scorer" in body
    assert 'classList.toggle("hidden"' in body
    # JSがやるのは表示制御だけ
    assert "apiFetch" not in body

    html = client.get(f"/host/{_create(client)['project_id']}").get_data(as_text=True)
    assert 'id="openHostScoringForm"' in html


def test_phase10a_adds_no_session_architecture_change():
    """同一ブラウザで複数Scorerを同時に扱う仕組みは今回入れない。"""
    for path in ("app/routes/api_auth.py", "app/routes/api_projects.py"):
        source = (Path(__file__).resolve().parent.parent / path).read_text(encoding="utf-8")
        assert "scorer_sessions" not in source
        assert "SESSION_COOKIE_NAME" not in source


# ---------------------------------------------------------------------------
# Phase 10A-6: Host Settings の「ホスト兼任の採点者」select
#
# DRAFTである限り、ホスト兼任が今いるかどうかに関係なく常に操作可能にする。
# allow_host_scoring が false だから disabled にする、という設計にはしない
# (ホスト兼任のScorerを削除した直後に誰も再割当できなくなるため)。
# ---------------------------------------------------------------------------


def _settings_js() -> str:
    return (JS_DIR / "host_settings.js").read_text(encoding="utf-8")


def _render_host_scorer_body() -> str:
    js = _settings_js()
    body = js[js.index("function renderHostScorer(isDraft) {"):]
    return body[: body.index("\n  }")]


def test_settings_select_is_never_disabled_by_allow_host_scoring():
    """★ allow_host_scoring を理由に disabled にしていないこと。"""
    body = _render_host_scorer_body()
    assert "allow_host_scoring" not in body
    # disabled の根拠は DRAFT かどうかと、候補が0人かどうかだけ
    assert "select.disabled = !isDraft" in body
    assert "select.disabled = empty" in body


def test_settings_select_lists_only_active_scorers():
    body = _render_host_scorer_body()
    assert "filter((scorer) => scorer.is_active)" in body
    assert '"(なし)"' in body


def test_settings_select_exists_in_the_template(client, db):
    created = _create(client, allow_host_scoring=False)
    html = client.get(f"/host/{created['project_id']}/settings").get_data(as_text=True)
    for element_id in (
        "hostScorerSection",
        "hostScorerSelect",
        "saveHostScorerButton",
        "hostScorerEmptyNote",
    ):
        assert f'id="{element_id}"' in html, element_id
    # テンプレート側で disabled を固定していない
    tag = re.search(r'<select id="hostScorerSelect"[^>]*>', html)
    assert tag and "disabled" not in tag.group(0)


def test_deleting_the_host_scorer_leaves_a_reassignable_state(client, db):
    """★ 田中(兼任)を削除 → なし → 吉田へ再割当できる、の一連。"""
    created = create_project(
        client, scorers=["吉田", "田中"], allow_host_scoring=True, host_scorer_index=1
    )
    project_id = created["project_id"]
    tanaka = _host_scorers(project_id)[0]
    assert tanaka.display_name == "田中"

    # 1) 兼任者を削除する
    assert client.delete(
        f"/api/projects/{project_id}/scorers/{tanaka.id}"
    ).status_code == 204

    detail = client.get(f"/api/projects/{project_id}").get_json()
    assert detail["status"] == "DRAFT"
    assert detail["allow_host_scoring"] is False
    # 2) 候補として残るのは吉田だけ。削除済みの田中は出てこない。
    candidates = [s for s in detail["scorers"] if s["is_active"]]
    assert [s["display_name"] for s in candidates] == ["吉田"]
    assert all(not s["is_host_scorer"] for s in detail["scorers"])

    # 3) 吉田へ再割当できる
    yoshida = candidates[0]
    assert _patch_host_scorer(client, project_id, yoshida["id"]).status_code == 200
    detail = client.get(f"/api/projects/{project_id}").get_json()
    assert detail["allow_host_scoring"] is True
    assert [(s["display_name"], s["is_host_scorer"]) for s in detail["scorers"]] == [
        ("吉田", True)
    ]

    # 4) 解除して「なし」へ戻せる
    assert _patch_host_scorer(client, project_id, None).status_code == 200
    detail = client.get(f"/api/projects/{project_id}").get_json()
    assert detail["allow_host_scoring"] is False
    assert all(not s["is_host_scorer"] for s in detail["scorers"])


def test_reassignment_is_still_draft_only(client, db):
    """DRAFT以外では変更できないという既存制約は維持する。"""
    created = _create(client, allow_host_scoring=False)
    project_id = created["project_id"]
    start_scoring(client, project_id)

    target = _scorers(project_id)[0]
    assert _patch_host_scorer(client, project_id, target.id).status_code == 409

    body = _render_host_scorer_body()
    assert "select.disabled = !isDraft" in body
    assert "saveButton.disabled = !isDraft" in body


def test_settings_select_is_styled_for_the_dark_theme():
    """selectに明示的なstyleが無いと、暗い背景で選択肢が読めなくなる。"""
    css = (APP_DIR / "static" / "css" / "common.css").read_text(encoding="utf-8")
    assert re.search(r"^select \{", css, re.M), "select のスタイルが無い"
    block = css[css.index("select {"):]
    block = block[: block.index("}")]
    assert "background:" in block
    assert "color:" in block
    assert "select option" in css


# ---------------------------------------------------------------------------
# Phase 10A-6: Host Settings → Host Dashboard の戻る導線
# ---------------------------------------------------------------------------


def test_settings_has_a_back_link_to_the_dashboard(client, db):
    created = _create(client, allow_host_scoring=False)
    html = client.get(f"/host/{created['project_id']}/settings").get_data(as_text=True)

    tag = re.search(r'<a[^>]*id="hostDashboardLink"[^>]*>(.*?)</a>', html, re.S)
    assert tag, "hostDashboardLink が見つからない"
    assert "ホストダッシュボードに戻る" in tag.group(1)


def test_back_link_is_always_shown_even_while_draft(client, db):
    """★ DRAFT中こそ必要な導線なので、状態に関係なく常に出す。"""
    js = _settings_js()
    body = js[js.index("function render() {"):]
    body = body[: body.index("\n  }")]

    assert 'dashboardLink.href = `/host/${projectId}`' in body
    assert 'dashboardLink.classList.remove("hidden")' in body
    # 「DRAFTのときは出さない」という分岐が残っていないこと
    link_part = body[body.index("const dashboardLink"):]
    link_part = link_part[: link_part.index("draftOnlyNotice")]
    assert "isDraft" not in link_part


def test_back_link_is_a_plain_get_navigation(client, db):
    """状態遷移も保存もしない、素のGET遷移であること。"""
    js = _settings_js()
    body = js[js.index("function render() {"):]
    body = body[: body.index("\n  }")]
    link_part = body[body.index("const dashboardLink"):]
    link_part = link_part[: link_part.index("draftOnlyNotice")]

    for token in ("apiFetch", "method:", "transition", "addEventListener"):
        assert token not in link_part, token

    created = _create(client, allow_host_scoring=False)
    project_id = created["project_id"]
    # 遷移先は実在するルートで、開いても状態は変わらない
    assert client.get(f"/host/{project_id}").status_code == 200
    assert client.get(f"/api/projects/{project_id}").get_json()["status"] == "DRAFT"


def test_back_link_is_independent_from_start_scoring(client, db):
    """「採点を開始する」とは別物であること(DRAFT→SCORINGを伴わない)。"""
    created = _create(client, allow_host_scoring=False)
    html = client.get(f"/host/{created['project_id']}/settings").get_data(as_text=True)

    back = re.search(r'<a[^>]*id="hostDashboardLink"[^>]*>.*?</a>', html, re.S).group(0)
    assert "startScoringButton" not in back
    assert 'id="startScoringButton"' in html  # 開始ボタン自体は残っている

    # 戻る導線はページ上部(開始ボタンより前)にある
    assert html.index('id="hostDashboardLink"') < html.index('id="startScoringButton"')
