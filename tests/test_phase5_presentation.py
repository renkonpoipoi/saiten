"""Phase 5: Result Presentation(LOCKED->PRESENTING->FINISHED)のテスト。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.models import Criterion, Project

VALID_PAYLOAD = {
    "name": "発表テスト",
    "subjects": ["チームA", "チームB"],
    "scorers": ["採点者1"],
    "criteria": ["独創性", "実用性", "デザイン", "技術力", "拡張性"],
    "allow_host_scoring": False,
}


def _create_start_and_lock(client, app, payload=None):
    resp = client.post("/api/projects", json=payload or VALID_PAYLOAD)
    assert resp.status_code == 201
    data = resp.get_json()
    project_id = data["project_id"]

    client.post("/api/auth/host-login", json={"code": data["host_code"]})
    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})

    scorer_client = app.test_client()
    scorer_client.post("/api/auth/scorer-login", json={"code": data["scorers"][0]["code"]})
    criteria = Criterion.query.filter_by(project_id=project_id).order_by(Criterion.sort_order).all()
    dashboard = scorer_client.get("/api/scorer/me/evaluations").get_json()
    for row in dashboard["subjects"]:
        scores = {str(c.id): 18 for c in criteria}
        scorer_client.post(f"/api/evaluations/{row['evaluation_id']}/scores", json={"scores": scores})
        scorer_client.post(f"/api/evaluations/{row['evaluation_id']}/submit")

    lock_resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"}
    )
    assert lock_resp.status_code == 200
    return data


def test_result_summary_rejected_before_locked(client, app, db):
    resp = client.post("/api/projects", json=VALID_PAYLOAD)
    data = resp.get_json()
    project_id = data["project_id"]
    client.post("/api/auth/host-login", json={"code": data["host_code"]})
    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "SCORING"})

    resp = client.get(f"/api/projects/{project_id}/result-summary")
    assert resp.status_code == 409


def test_result_summary_requires_host_session(client, app, db):
    data = _create_start_and_lock(client, app)
    project_id = data["project_id"]

    anonymous_client = app.test_client()
    resp = anonymous_client.get(f"/api/projects/{project_id}/result-summary")
    assert resp.status_code == 403


def test_result_summary_rejects_other_project_host(client, app, db):
    data_a = _create_start_and_lock(client, app, dict(VALID_PAYLOAD, name="A"))
    client_b = app.test_client()
    data_b = _create_start_and_lock(client_b, app, dict(VALID_PAYLOAD, name="B"))

    # AのホストとしてログインしたclientでBのresult-summaryを取ろうとする
    resp = client.get(f"/api/projects/{data_b['project_id']}/result-summary")
    assert resp.status_code == 403


def test_locked_to_presenting_transition(client, app, db):
    data = _create_start_and_lock(client, app)
    project_id = data["project_id"]

    resp = client.post(
        f"/api/projects/{project_id}/transition", json={"target_status": "PRESENTING"}
    )
    assert resp.status_code == 200
    assert db.session.get(Project, project_id).status == "PRESENTING"

    resp = client.get(f"/api/projects/{project_id}/result-summary")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["project"]["status"] == "PRESENTING"
    assert len(body["subjects"]) == 2


def test_presenting_to_finished_and_reviewable(client, app, db):
    data = _create_start_and_lock(client, app)
    project_id = data["project_id"]
    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "PRESENTING"})

    resp = client.post(f"/api/projects/{project_id}/transition", json={"target_status": "FINISHED"})
    assert resp.status_code == 200
    assert db.session.get(Project, project_id).status == "FINISHED"

    # FINISHED後もHostは結果を再閲覧できる
    resp = client.get(f"/api/projects/{project_id}/result-summary")
    assert resp.status_code == 200
    assert resp.get_json()["project"]["status"] == "FINISHED"


def test_result_summary_includes_tie_ranking(client, app, db):
    payload = dict(VALID_PAYLOAD, subjects=["チームA", "チームB", "チームC"])
    data = _create_start_and_lock(client, app, payload)
    project_id = data["project_id"]

    resp = client.get(f"/api/projects/{project_id}/result-summary")
    assert resp.status_code == 200
    body = resp.get_json()
    # 全チーム同じ得点(18点固定)で提出しているため、全員1位のcompetition rankingになる
    assert all(s["rank"] == 1 for s in body["subjects"])
    assert all(s["total_score"] == 90 for s in body["subjects"])  # 5criteria x 18


# ---------------------------------------------------------------------------
# 静的アセット / 旧resultへの依存が無いことの確認
# ---------------------------------------------------------------------------


def test_broadcast_derived_audio_assets_are_gone(client):
    """Phase 9: 実在人物の放送音声由来だった暫定素材への依存を外した。

    Phase 5では暫定素材2件の配信を検証していたが、Phase 9で正式に削除し、
    効果音はマニフェスト駆動(素材0個でも完全動作)に置き換えた。
    """
    assert client.get("/static/assets/reveal-hit.m4a").status_code == 404
    assert client.get("/static/assets/reveal-sting.m4a").status_code == 404
    # 置き換え先のマニフェストは常に配信される
    assert client.get("/static/assets/sfx/manifest.js").status_code == 200


def test_js_files_have_no_syntax_errors():
    js_dir = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    for js_file in js_dir.rglob("*.js"):
        result = subprocess.run(
            ["node", "--check", str(js_file)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{js_file.name}: {result.stderr}"


def test_no_external_http_proxy_dependency():
    """旧resultアプリはSCORE_SOURCE_BASE_URL経由でurllibによるHTTPプロキシを
    行っていたが、新実装はresult_service経由で同一プロセス内DBから直接
    集計するため、そのような外部HTTP依存が無いことを確認する。
    """
    app_dir = Path(__file__).resolve().parent.parent / "app"
    forbidden_tokens = ("urllib.request", "SCORE_SOURCE_BASE_URL", "requests.get(")
    for py_file in app_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in content, f"{py_file}: unexpected external HTTP dependency ({token})"
