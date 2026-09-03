"""Phase 8で追加したテスト専用の共有ヘルパー。

Phase 1〜7のテストは意図的にこのモジュールを使わない。既存テストを無変更のまま
通し続けること自体をBATCH無回帰の証拠として維持するため、既存の重複ヘルパーは
リファクタしない。
"""

from __future__ import annotations

from app.models import Criterion, Subject

DEFAULT_CRITERIA = ["独創性", "実用性", "デザイン", "技術力", "拡張性"]


def build_payload(**overrides) -> dict:
    payload = {
        "name": "Phase8テスト",
        "subjects": ["チームA", "チームB"],
        "scorers": ["採点者1", "採点者2"],
        "criteria": list(DEFAULT_CRITERIA),
        "allow_host_scoring": False,
    }
    payload.update(overrides)
    return payload


def create_project(client, **overrides) -> dict:
    """プロジェクトを作成して作成APIのレスポンスを返す。

    Phase 8A-1以降、作成APIは呼び出したclientにHost sessionを張るため、
    別途host-loginしなくてもHost APIを使える。
    """
    resp = client.post("/api/projects", json=build_payload(**overrides))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def login_host(client, host_code: str) -> None:
    resp = client.post("/api/auth/host-login", json={"code": host_code})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def login_scorer(scorer_client, code: str) -> dict:
    resp = scorer_client.post("/api/auth/scorer-login", json={"code": code})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def start_scoring(client, project_id: int) -> None:
    resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"}
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def criteria_for(project_id: int) -> list[Criterion]:
    return (
        Criterion.query.filter_by(project_id=project_id)
        .order_by(Criterion.sort_order)
        .all()
    )


def subjects_for(project_id: int) -> list[Subject]:
    return (
        Subject.query.filter_by(project_id=project_id).order_by(Subject.sort_order).all()
    )


def evaluation_id_for(scorer_client, subject_id: int) -> int:
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    for row in dashboard["subjects"]:
        if row["subject_id"] == subject_id:
            return row["evaluation_id"]
    raise AssertionError(f"subject {subject_id} is not assigned to this scorer")


def score_and_submit(scorer_client, project_id: int, subject_id: int, value: int = 15,
                     feedback: str | None = None):
    """1 Subjectを満点未満の一定値で採点して確定する。戻り値はsubmitのresponse。"""
    evaluation_id = evaluation_id_for(scorer_client, subject_id)
    payload = {"scores": {str(c.id): value for c in criteria_for(project_id)}}
    if feedback is not None:
        payload["feedback"] = feedback
    resp = scorer_client.post(f"/api/evaluations/{evaluation_id}/scores", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return scorer_client.post(f"/api/evaluations/{evaluation_id}/submit")


def scorer_clients(app, created: dict) -> list:
    """作成レスポンスの全scorerについて、ログイン済みのtest clientを返す。"""
    clients = []
    for scorer in created["scorers"]:
        c = app.test_client()
        login_scorer(c, scorer["code"])
        clients.append(c)
    return clients
