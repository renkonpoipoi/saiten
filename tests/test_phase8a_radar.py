"""Phase 8A-4: 採点軸別平均のレーダーチャート(自前SVG)。

座標計算は純粋関数なのでnodeで直接実行して検証する。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from tests.helpers import (
    create_project,
    login_scorer,
    score_and_submit,
    start_scoring,
    subjects_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"
RADAR_JS = APP_DIR / "static" / "js" / "radar_chart.js"


def _run_radar_js(script: str) -> dict:
    """radar_chart.jsを読み込み、純粋関数部分をnodeで評価する。"""
    harness = f"""
    globalThis.window = {{}};
    require({str(RADAR_JS)!r});
    const RadarChart = globalThis.window.RadarChart;
    {script}
    """
    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _locked_project(client, app):
    created = create_project(client)
    project_id = created["project_id"]
    start_scoring(client, project_id)
    for scorer_info in created["scorers"]:
        scorer = app.test_client()
        login_scorer(scorer, scorer_info["code"])
        for subject in subjects_for(project_id):
            score_and_submit(scorer, project_id, subject.id, value=10)
    client.post(f"/api/projects/{project_id}/transition", json={"target_status": "LOCKED"})
    return created


# ---------------------------------------------------------------------------
# 座標計算(純粋関数)
# ---------------------------------------------------------------------------


def test_radar_points_start_at_twelve_oclock():
    points = _run_radar_js(
        "console.log(JSON.stringify("
        "RadarChart.radarPoints([20,20,20,20,20],[20,20,20,20,20],100,100,80)));"
    )
    assert len(points) == 5
    # 最初の軸は真上
    assert round(points[0]["x"], 6) == 100
    assert round(points[0]["y"], 6) == 20


def test_radar_points_scale_by_max_score():
    points = _run_radar_js(
        "console.log(JSON.stringify("
        "RadarChart.radarPoints([10,0,20,20,20],[20,20,20,20,20],100,100,80)));"
    )
    # 平均10/満点20 -> 半径の50%
    assert round(points[0]["y"], 6) == 60
    # 0点は中心
    assert round(points[1]["x"], 6) == 100
    assert round(points[1]["y"], 6) == 100


def test_radar_points_axis_count_is_derived_from_input():
    for count in (3, 5, 7):
        points = _run_radar_js(
            "console.log(JSON.stringify(RadarChart.radarPoints("
            f"new Array({count}).fill(1), new Array({count}).fill(1), 50, 50, 40)));"
        )
        assert len(points) == count
        # 全て半径40の円周上にある
        for point in points:
            distance = ((point["x"] - 50) ** 2 + (point["y"] - 50) ** 2) ** 0.5
            assert abs(distance - 40) < 1e-6


def test_radar_points_clamp_out_of_range_values():
    points = _run_radar_js(
        "console.log(JSON.stringify("
        "RadarChart.radarPoints([40,-5,20],[20,20,20],100,100,80)));"
    )
    # 満点超過は外周まで、負値は中心まででクランプされる
    assert round(points[0]["y"], 6) == 20
    assert round(points[1]["x"], 6) == 100 and round(points[1]["y"], 6) == 100


def test_radar_points_handle_zero_max_score():
    points = _run_radar_js(
        "console.log(JSON.stringify(RadarChart.radarPoints([5,5,5],[0,0,0],10,10,5)));"
    )
    for point in points:
        assert round(point["x"], 6) == 10
        assert round(point["y"], 6) == 10


# ---------------------------------------------------------------------------
# 実装方針(外部ライブラリを足さない)
# ---------------------------------------------------------------------------


def test_radar_chart_uses_inline_svg_without_dependencies():
    source = RADAR_JS.read_text(encoding="utf-8")
    assert "createElementNS" in source
    assert "http://www.w3.org/2000/svg" in source
    # 外部からコードを読み込む経路が無いこと
    for forbidden in ("import ", "require(", "cdn.", "unpkg", "jsdelivr",
                      "new Chart(", "d3.select", "<script"):
        assert forbidden not in source, forbidden


def test_no_chart_library_was_added_to_requirements():
    reqs = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("chart", "matplotlib", "plotly", "pandas", "numpy"):
        assert package not in reqs, package


def test_radar_axis_count_is_not_hardcoded_to_five():
    source = RADAR_JS.read_text(encoding="utf-8")
    assert "criterionAverages.length" in source
    assert "values.length" in source


def test_radar_labels_are_wrapped_not_truncated():
    """長い軸名は折り返すだけで、1文字も失わないこと。"""
    short = _run_radar_js(
        'console.log(JSON.stringify(RadarChart.wrapLabel("独創性")));'
    )
    assert short == ["独創性"]

    long_label = "とても長い採点軸の名前をつけた場合"
    wrapped = _run_radar_js(
        f'console.log(JSON.stringify(RadarChart.wrapLabel({long_label!r})));'
    )
    assert len(wrapped) > 1, "折り返されていない"
    assert "".join(wrapped) == long_label, "折り返しで文字が失われている"
    assert all(len(line) <= 8 for line in wrapped)

    source = RADAR_JS.read_text(encoding="utf-8")
    assert "tspan" in source, "複数行はtspanで描く"


# ---------------------------------------------------------------------------
# 画面への組み込み
# ---------------------------------------------------------------------------


def test_analysis_page_loads_radar_chart_before_its_user(client, app, db):
    created = _locked_project(client, app)
    html = client.get(f"/host/{created['project_id']}/analysis").get_data(as_text=True)

    radar_index = html.find("radar_chart.js")
    analysis_index = html.find("host_analysis.js")
    assert radar_index != -1 and analysis_index != -1
    assert radar_index < analysis_index, "radar_chart.jsはhost_analysis.jsより先に読む必要がある"


def test_radar_chart_asset_is_served(client):
    assert client.get("/static/js/radar_chart.js").status_code == 200


def test_analysis_js_renders_radar_from_criterion_averages():
    js = (APP_DIR / "static" / "js" / "host_analysis.js").read_text(encoding="utf-8")
    assert "RadarChart.render" in js
    assert "subject.criterion_averages" in js


def test_radar_is_not_added_to_presentation_page(client, app, db):
    """M1風の発表画面にはRadarを入れない(Analysis専用)。"""
    created = _locked_project(client, app)
    html = client.get(f"/host/{created['project_id']}/present").get_data(as_text=True)
    assert "radar_chart.js" not in html

    presentation_js = (
        APP_DIR / "static" / "js" / "result_presentation.js"
    ).read_text(encoding="utf-8")
    assert "RadarChart" not in presentation_js


def test_radar_styles_are_defined():
    css = (APP_DIR / "static" / "css" / "common.css").read_text(encoding="utf-8")
    for selector in (".radar-chart", ".radar-ring", ".radar-axis", ".radar-area",
                     ".radar-dot", ".radar-label"):
        assert selector in css, selector


def test_analysis_page_assets_all_resolve(client, app, db):
    created = _locked_project(client, app)
    html = client.get(f"/host/{created['project_id']}/analysis").get_data(as_text=True)
    for href in re.findall(r'(?:href|src)="(/static/[^"]+)"', html):
        assert client.get(href).status_code == 200, href
