"""Phase 9: Presentation engine の純粋関数を Node のテストランナーで検証する。

演出ロジック(timing / rank group / layout / step順序)は DOM もタイマーも
使わない純粋関数として app/static/js/presentation/core.js に置いてある。
その検証は tests/js/core.test.mjs にあり、ここでは pytest 一発で全部走るよう
subprocess で呼び出すだけにしている(既存の node --check と同じ流儀)。

「実時間を待つ脆いテスト」を書かないための設計上の要:
演出は「step descriptor を返す純粋関数」と「それを実行する runner」に
分離してあるので、順序・尺・分岐は配列の比較だけで検証できる。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_TEST_GLOB = "tests/js/*.test.mjs"


def _node() -> str:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI/dev環境にnodeが無い場合
        pytest.skip("node is not available")
    return node


def test_presentation_core_js_unit_tests_pass():
    result = subprocess.run(
        [_node(), "--test", JS_TEST_GLOB],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # 空振り(0件成功)を成功と誤認しないこと
    assert "# pass 0" not in result.stdout
    assert "fail 0" in result.stdout


def test_js_unit_tests_exist_for_the_presentation_core():
    files = sorted(p.name for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    assert "core.test.mjs" in files, files


def test_presentation_core_is_pure_and_environment_agnostic():
    """core.js は DOM / タイマー / 通信に触れない(だから Node で単体テストできる)。"""
    source = (ROOT / "app" / "static" / "js" / "presentation" / "core.js").read_text(
        encoding="utf-8"
    )
    for token in (
        "document.",
        "setTimeout",
        "setInterval",
        "requestAnimationFrame",
        "apiFetch",
        "fetch(",
        "XMLHttpRequest",
    ):
        assert token not in source, token
