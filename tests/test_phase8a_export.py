"""Phase 8A-3: CSV / Markdown export。"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from tests.helpers import (
    create_project,
    login_host,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"

CSV_HEADER = [
    "プロジェクト",
    "被採点者",
    "採点者",
    "公式集計対象",
    "独創性",
    "実用性",
    "デザイン",
    "技術力",
    "拡張性",
    "合計",
    "フィードバック",
    "提出日時",
]


def _locked_project(client, app, *, feedbacks=None):
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    for index, scorer_info in enumerate(created["scorers"]):
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        for subject in subjects:
            feedback = (feedbacks or {}).get(
                (scorer_info["display_name"], subject.name),
                f"{scorer_info['display_name']}の講評",
            )
            score_and_submit(
                scorer, project_id, subject.id, value=20 - index * 5, feedback=feedback
            )

    assert (
        client.post(
            f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"}
        ).status_code
        == 200
    )
    return created


def _forced_closed_project(client, app):
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    subjects = subjects_for(project_id)

    complete = app.test_client()
    login_scorer(complete, created["scorers"][0]["code"])
    for subject in subjects:
        score_and_submit(complete, project_id, subject.id, value=20, feedback="完走者の講評")

    partial = app.test_client()
    login_scorer(partial, created["scorers"][1]["code"])
    score_and_submit(partial, project_id, subjects[0].id, value=4, feedback="未完了者の講評")

    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"})
    return created


def _read_csv(body: str) -> list[list[str]]:
    assert body.startswith("﻿"), "UTF-8 BOMが無い"
    return list(csv.reader(io.StringIO(body[1:])))


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_csv_export_returns_utf8_bom_and_crlf(client, app, db):
    created = _locked_project(client, app)
    resp = client.get(f"/api/projects/{created['project_id']}/export.csv")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "text/csv; charset=utf-8"

    body = resp.get_data(as_text=True)
    assert body.startswith("﻿")
    assert "\r\n" in body
    # LFのみの改行が混ざっていないこと
    assert body.replace("\r\n", "").count("\n") == 0


def test_csv_export_has_expected_header(client, app, db):
    created = _locked_project(client, app)
    rows = _read_csv(
        client.get(f"/api/projects/{created['project_id']}/export.csv").get_data(as_text=True)
    )
    assert rows[0] == CSV_HEADER


def test_csv_export_is_one_row_per_submitted_evaluation(client, app, db):
    created = _locked_project(client, app)
    rows = _read_csv(
        client.get(f"/api/projects/{created['project_id']}/export.csv").get_data(as_text=True)
    )
    # 2 scorer x 2 subject = 4行 + ヘッダ
    assert len(rows) == 5

    data_rows = rows[1:]
    assert all(row[0] == "Phase8テスト" for row in data_rows)
    assert {row[1] for row in data_rows} == {"チームA", "チームB"}
    assert {row[2] for row in data_rows} == {"採点者1", "採点者2"}
    for row in data_rows:
        scores = [int(v) for v in row[4:9]]
        assert len(scores) == 5
        assert int(row[9]) == sum(scores)
        assert row[10].endswith("の講評")
        assert row[11]  # 提出日時


def test_csv_export_marks_official_inclusion(client, app, db):
    created = _forced_closed_project(client, app)
    rows = _read_csv(
        client.get(f"/api/projects/{created['project_id']}/export.csv").get_data(as_text=True)
    )
    by_scorer = {}
    for row in rows[1:]:
        by_scorer.setdefault(row[2], set()).add(row[3])

    assert by_scorer["採点者1"] == {"対象"}
    assert by_scorer["採点者2"] == {"対象外"}
    # 除外された採点者のfeedbackも保持されている
    assert any(row[10] == "未完了者の講評" for row in rows[1:])


def test_csv_export_escapes_formula_injection(client, app, db):
    created = _locked_project(
        client,
        app,
        feedbacks={("採点者1", "チームA"): '=HYPERLINK("http://evil","click")'},
    )
    rows = _read_csv(
        client.get(f"/api/projects/{created['project_id']}/export.csv").get_data(as_text=True)
    )
    dangerous = [row[10] for row in rows[1:] if "HYPERLINK" in row[10]]
    assert dangerous, "対象のfeedbackが出力されていない"
    for value in dangerous:
        assert value.startswith("'="), value


def test_csv_export_escapes_all_formula_prefixes():
    from app.services.export_service import _escape_formula

    for prefix in ("=", "+", "-", "@", "\t", "\r"):
        assert _escape_formula(f"{prefix}danger").startswith("'" + prefix)
    assert _escape_formula("ふつうの講評") == "ふつうの講評"
    assert _escape_formula(100) == "100"
    assert _escape_formula(None) == ""


def test_csv_export_has_download_headers(client, app, db):
    created = _locked_project(client, app)
    resp = client.get(f"/api/projects/{created['project_id']}/export.csv")
    disposition = resp.headers["Content-Disposition"]

    assert disposition.startswith("attachment;")
    assert f'filename="project_{created["project_id"]}_results.csv"' in disposition
    assert "filename*=UTF-8''" in disposition


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def test_markdown_export_structure(client, app, db):
    created = _locked_project(client, app)
    resp = client.get(f"/api/projects/{created['project_id']}/export.md")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert "attachment;" in resp.headers["Content-Disposition"]

    body = resp.get_data(as_text=True)
    assert body.startswith("# Phase8テスト")
    assert "## 最終ランキング" in body
    assert "### 採点軸別平均" in body
    assert "### 採点者別合計" in body
    assert "### フィードバック" in body
    assert "## チームA" in body
    assert "## チームB" in body
    assert "採点者1の講評" in body


def test_markdown_export_marks_excluded_scorers(client, app, db):
    created = _forced_closed_project(client, app)
    body = client.get(
        f"/api/projects/{created['project_id']}/export.md"
    ).get_data(as_text=True)

    assert "公式集計対象" in body
    assert "公式集計対象外" in body
    assert "未完了者の講評" in body


def test_markdown_and_csv_agree_with_analysis(client, app, db):
    """3者が同じbuild_analysis()を入力にしているため数値が一致すること。"""
    created = _locked_project(client, app)
    project_id = created["project_id"]

    analysis = client.get(f"/api/projects/{project_id}/analysis").get_json()
    markdown = client.get(f"/api/projects/{project_id}/export.md").get_data(as_text=True)
    rows = _read_csv(
        client.get(f"/api/projects/{project_id}/export.csv").get_data(as_text=True)
    )

    for subject in analysis["subjects"]:
        assert (
            f"| {subject['rank']} | {subject['name']} | "
            f"{subject['total_score']} | {subject['mean_score']} |" in markdown
        )
        csv_total = sum(
            int(row[9]) for row in rows[1:]
            if row[1] == subject["name"] and row[3] == "対象"
        )
        assert csv_total == subject["total_score"], subject["name"]


# ---------------------------------------------------------------------------
# authorization / status gate / secret
# ---------------------------------------------------------------------------


def test_exports_require_host_session(client, app, db):
    created = _locked_project(client, app)
    anonymous = app.test_client()
    for path in ("export.csv", "export.md"):
        resp = anonymous.get(f"/api/projects/{created['project_id']}/{path}")
        assert resp.status_code == 403, path


def test_exports_reject_other_project_host(client, app, db):
    mine = _locked_project(client, app)
    other_client = app.test_client()
    theirs = _locked_project(other_client, app)

    login_host(client, mine["host_code"])
    for path in ("export.csv", "export.md"):
        resp = client.get(f"/api/projects/{theirs['project_id']}/{path}")
        assert resp.status_code == 403, path


def test_exports_rejected_before_locked(client, db):
    created = create_project(client)
    project_id = created["project_id"]
    for path in ("export.csv", "export.md"):
        assert client.get(f"/api/projects/{project_id}/{path}").status_code == 409, path

    start_scoring(client, project_id)
    for path in ("export.csv", "export.md"):
        assert client.get(f"/api/projects/{project_id}/{path}").status_code == 409, path


def test_exports_never_contain_secrets(client, app, db):
    created = _locked_project(client, app)
    project_id = created["project_id"]

    for path in ("export.csv", "export.md"):
        body = client.get(f"/api/projects/{project_id}/{path}").get_data(as_text=True)
        assert created["host_code"] not in body, path
        for scorer in created["scorers"]:
            assert scorer["code"] not in body, path
        for token in ("host_code_hash", "access_code_hash", "host_", "scr_"):
            assert token not in body, f"{path}: {token}"


def test_export_service_takes_only_analysis_output():
    """export_serviceがモデルを直接触らないこと(ホワイトリスト出力の担保)。"""
    source = (APP_DIR / "services" / "export_service.py").read_text(encoding="utf-8")
    for token in ("from app.models", "Scorer", "Evaluation", "query", "db."):
        assert token not in source, token


def test_analysis_page_exposes_export_links(client, app, db):
    created = _locked_project(client, app)
    html = client.get(f"/host/{created['project_id']}/analysis").get_data(as_text=True)
    assert 'id="exportCsvLink"' in html
    assert 'id="exportMarkdownLink"' in html
