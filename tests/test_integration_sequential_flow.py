"""SEQUENTIAL modeを通しで検証するend-to-end統合テスト。

Project作成(SEQUENTIAL) -> 作成者の自動Host session -> DRAFT->SCORING
-> Subject Aを全Scorerが採点 -> A LOCKED -> Judge単位のReveal data -> A PRESENTED
-> Bが自動でSCORING -> B, C も同様 -> 全Subject PRESENTED -> Project LOCKED
-> PRESENTING -> competition ranking -> FINISHED -> Replay -> Analysis
-> CSV / Markdown export、までを1本で通す。

Subjectの発表が進んでいる間、Project statusが一貫してSCORINGのままであること
(=不可逆な状態機械を往復させていないこと)もあわせて確認する。
"""

from __future__ import annotations

import csv
import io

from app.models import Project, Subject
from app.services.code_service import hash_code
from tests.helpers import (
    criteria_for,
    evaluation_id_for,
    login_scorer,
    subjects_for,
)

PAYLOAD = {
    "name": "逐次発表統合テスト",
    "subjects": ["チームA", "チームB", "チームC"],
    "scorers": ["採点者1", "採点者2", "採点者3"],
    "criteria": ["独創性", "実用性", "デザイン", "技術力", "拡張性"],
    "allow_host_scoring": False,
    "presentation_mode": "SEQUENTIAL",
}

# チームA/Bは同点(3人とも20点)、チームCだけ低くして competition ranking を作る
SUBJECT_SCORES = {"チームA": 20, "チームB": 20, "チームC": 8}


def test_sequential_full_lifecycle(client, app, db):
    # --- 1. 作成(SEQUENTIAL)。作成者はそのままHostになる ---
    create_resp = client.post("/api/projects", json=PAYLOAD)
    assert create_resp.status_code == 201
    created = create_resp.get_json()
    project_id = created["project_id"]
    assert created["presentation_mode"] == "SEQUENTIAL"

    # host codeはhashのみ保存され、平文はDBに残らない
    project = db.session.get(Project, project_id)
    assert project.host_code_hash == hash_code(created["host_code"])

    # host-loginを経ずにHost APIが使える
    assert client.get(f"/api/projects/{project_id}/progress").status_code == 200

    # --- 2. DRAFT -> SCORING。最初のSubjectだけ採点可能になる ---
    assert (
        client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"}
        ).status_code
        == 200
    )
    subjects = subjects_for(project_id)
    assert [s.presentation_status for s in subjects] == ["SCORING", "WAITING", "WAITING"]

    scorer_clients = []
    for scorer_info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        scorer_clients.append(scorer)

    criteria = criteria_for(project_id)
    participating = {s["id"] for s in created["scorers"]}

    # 先行採点は拒否される
    ahead = evaluation_id_for(scorer_clients[0], subjects[1].id)
    assert (
        scorer_clients[0]
        .post(
            f"/api/evaluations/{ahead}/scores",
            json={"scores": {str(c.id): 20 for c in criteria}},
        )
        .status_code
        == 409
    )

    # --- 3. Subjectを1つずつ 採点 -> 締切 -> 発表 ---
    for index, subject in enumerate(subjects):
        value = SUBJECT_SCORES[subject.name]

        # 全員が提出するまでは締め切れない(forced closeは無い)
        assert (
            client.post(
                f"/api/projects/{project_id}/subjects/{subject.id}/lock"
            ).status_code
            == 409
        )

        for scorer in scorer_clients:
            evaluation_id = evaluation_id_for(scorer, subject.id)
            assert (
                scorer.post(
                    f"/api/evaluations/{evaluation_id}/scores",
                    json={
                        "scores": {str(c.id): value for c in criteria},
                        "feedback": f"{subject.name}への講評",
                    },
                ).status_code
                == 200
            )
            assert scorer.post(f"/api/evaluations/{evaluation_id}/submit").status_code == 200

        # 全員提出済みになったのでHostが締め切れる
        progress = client.get(f"/api/projects/{project_id}/progress").get_json()
        row = next(r for r in progress["subjects"] if r["id"] == subject.id)
        assert row["submitted_count"] == row["scorer_count"] == 3
        assert row["can_lock"] is True

        assert (
            client.post(
                f"/api/projects/{project_id}/subjects/{subject.id}/lock"
            ).status_code
            == 200
        )

        # 締切後は当該Subjectへの書き込みが拒否される
        locked_eval = evaluation_id_for(scorer_clients[0], subject.id)
        assert (
            scorer_clients[0]
            .post(
                f"/api/evaluations/{locked_eval}/scores",
                json={"scores": {str(c.id): 1 for c in criteria}},
            )
            .status_code
            == 409
        )

        # --- 発表データ: 審査員ごとの得点とSubject TOTAL ---
        payload = client.get(
            f"/api/projects/{project_id}/subjects/{subject.id}/result"
        ).get_json()
        revealed = payload["subject"]
        assert payload["theoretical_max_total"] == 100
        assert revealed["scorer_count"] == 3
        assert [j["total"] for j in revealed["judge_totals"]] == [value * 5] * 3
        assert [j["scorer_id"] for j in revealed["judge_totals"]] == sorted(participating)
        assert sum(j["total"] for j in revealed["judge_totals"]) == revealed["total_score"]

        # --- 発表確定。次のSubjectが自動で採点可能になる ---
        present = client.post(
            f"/api/projects/{project_id}/subjects/{subject.id}/present"
        ).get_json()
        assert present["subject"]["presentation_status"] == "PRESENTED"

        is_last = index == len(subjects) - 1
        if is_last:
            assert present["next_subject"] is None
        else:
            assert present["next_subject"]["id"] == subjects[index + 1].id
            assert present["next_subject"]["presentation_status"] == "SCORING"

        # Subject発表中、Project statusは一度もSCORINGから動かない
        assert db.session.get(Project, project_id).status == "SCORING"

        # 参加Scorer集合は最後まで変化しない
        from app.services.project_service import participating_scorer_ids

        assert participating_scorer_ids(project_id) == participating

    assert [
        db.session.get(Subject, s.id).presentation_status for s in subjects
    ] == ["PRESENTED"] * 3

    # --- 4. 最終ランキングへ(前向きにのみ遷移) ---
    state = client.get(f"/api/projects/{project_id}/presentation-state").get_json()
    assert state["all_subjects_presented"] is True

    for target in ("LOCKED", "PRESENTING"):
        assert (
            client.post(
                f"/api/projects/{project_id}/transition", json={"target_status": target}
            ).status_code
            == 200
        )

    summary = client.get(f"/api/projects/{project_id}/result-summary").get_json()
    by_name = {s["name"]: s for s in summary["subjects"]}
    assert summary["official_scorer_count"] == 3
    assert by_name["チームA"]["total_score"] == 300  # 20 x 5軸 x 3人
    assert by_name["チームB"]["total_score"] == 300
    assert by_name["チームC"]["total_score"] == 120  # 8 x 5軸 x 3人
    # competition ranking: A,Bが同点1位、Cが3位(2位は欠番)
    assert by_name["チームA"]["rank"] == 1
    assert by_name["チームB"]["rank"] == 1
    assert by_name["チームC"]["rank"] == 3
    # 全Subjectが同じ人数で採点されている
    assert {s["scorer_count"] for s in summary["subjects"]} == {3}

    # --- 5. FINISHED ---
    assert (
        client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": "FINISHED"}
        ).status_code
        == 200
    )
    assert db.session.get(Project, project_id).status == "FINISHED"

    # --- 6. Replay: 何度取得してもFINISHEDのまま ---
    for _ in range(3):
        replay = client.get(f"/api/projects/{project_id}/result-summary")
        assert replay.status_code == 200
        assert replay.get_json()["subjects"] == summary["subjects"]
        assert db.session.get(Project, project_id).status == "FINISHED"

    # 巻き戻しは拒否される
    for target in ("PRESENTING", "LOCKED", "SCORING", "DRAFT"):
        assert (
            client.post(
                f"/api/projects/{project_id}/transition", json={"target_status": target}
            ).status_code
            == 409
        )

    # --- 7. Analysis ---
    analysis = client.get(f"/api/projects/{project_id}/analysis").get_json()
    assert analysis["project"]["presentation_mode"] == "SEQUENTIAL"
    assert analysis["official_scorer_count"] == 3
    # SEQUENTIALでは全員が公式集計対象なので除外者は出ない
    assert analysis["excluded_scorer_count"] == 0
    for subject in analysis["subjects"]:
        assert len(subject["evaluations"]) == 3
        assert all(e["official_included"] for e in subject["evaluations"])
        assert all(
            e["feedback"] == f"{subject['name']}への講評" for e in subject["evaluations"]
        )
        assert len(subject["criterion_averages"]) == 5
        assert all("name" in a and "max_score" in a for a in subject["criterion_averages"])

    # --- 8. CSV export ---
    csv_resp = client.get(f"/api/projects/{project_id}/export.csv")
    assert csv_resp.status_code == 200
    body = csv_resp.get_data(as_text=True)
    assert body.startswith("﻿")  # UTF-8 BOM
    rows = list(csv.reader(io.StringIO(body[1:])))
    # 3 subject x 3 scorer = 9行 + ヘッダ
    assert len(rows) == 10
    assert rows[0][:4] == ["プロジェクト", "被採点者", "採点者", "公式集計対象"]
    assert all(row[3] == "対象" for row in rows[1:])

    # --- 9. Markdown export ---
    md = client.get(f"/api/projects/{project_id}/export.md").get_data(as_text=True)
    assert md.startswith("# 逐次発表統合テスト")
    assert "発表者ごとに採点・発表" in md
    assert "## 最終ランキング" in md
    assert "| 1 | チームA | 300 | 100.0 |" in md

    # --- 10. secretがexportされていない ---
    for exported in (body, md):
        assert created["host_code"] not in exported
        for scorer_info in created["scorers"]:
            assert scorer_info["code"] not in exported

    # --- 11. cross-project authorization ---
    other = app.test_client()
    other_created = other.post("/api/projects", json=dict(PAYLOAD, name="別物")).get_json()
    for path in ("result-summary", "analysis", "export.csv", "export.md",
                 "presentation-state"):
        assert (
            client.get(f"/api/projects/{other_created['project_id']}/{path}").status_code
            == 403
        ), path
