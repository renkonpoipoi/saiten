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
