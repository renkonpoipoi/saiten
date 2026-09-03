"""採点結果のCSV / Markdown出力。

入力は必ず result_service.build_analysis() の戻り値だけにする。分析画面・CSV・
Markdownが同じ集計結果から作られるため、3者の数値が食い違うことがない。

出力する列は明示的なホワイトリストで、モデルを全カラム走査しない。
host_code / access_code とそのhashは、いかなる形式でも出力しない。
"""

from __future__ import annotations

import csv
import io
from urllib.parse import quote

# UTF-8 BOM。これが無いと日本語WindowsのExcelがCP932として読み、文字化けする。
UTF8_BOM = "﻿"

# 表計算ソフトが数式として解釈しうる先頭文字。値の先頭がこれらの場合、
# シングルクォートを付けて必ず文字列として扱わせる(CSV injection対策)。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _escape_formula(value) -> str:
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _format_timestamp(iso_value: str | None) -> str:
    """ISO8601から秒までの表記にする(マイクロ秒はノイズなので落とす)。"""
    if not iso_value:
        return ""
    head, _, tail = iso_value.partition(".")
    if not tail:
        return iso_value
    # マイクロ秒部分を捨て、タイムゾーン表記があれば残す
    offset = ""
    for marker in ("+", "-"):
        index = tail.find(marker)
        if index != -1:
            offset = tail[index:]
            break
    if tail.endswith("Z"):
        offset = "Z"
    return head + offset


def _official_label(official_included: bool) -> str:
    return "対象" if official_included else "対象外"


def content_disposition(filename_ascii: str, filename_utf8: str) -> str:
    """日本語ファイル名はRFC 5987で、非対応クライアント向けにASCII名も付ける。"""
    quoted = quote(filename_utf8, safe="")
    return f"attachment; filename=\"{filename_ascii}\"; filename*=UTF-8''{quoted}"


def csv_filename(analysis: dict) -> tuple[str, str]:
    project_id = analysis["project"]["id"]
    return f"project_{project_id}_results.csv", f"{analysis['project']['name']}_採点結果.csv"


def markdown_filename(analysis: dict) -> tuple[str, str]:
    project_id = analysis["project"]["id"]
    return f"project_{project_id}_results.md", f"{analysis['project']['name']}_採点結果.md"


def build_csv(analysis: dict) -> str:
    """1 submitted evaluation = 1 row のCSVを返す。"""
    criteria = analysis["criteria"]
    project_name = analysis["project"]["name"]

    buffer = io.StringIO()
    # Excelは行区切りにCRLFを期待する
    writer = csv.writer(buffer, lineterminator="\r\n")

    writer.writerow(
        [
            "プロジェクト",
            "被採点者",
            "採点者",
            "公式集計対象",
            *[c["name"] for c in criteria],
            "合計",
            "フィードバック",
            "提出日時",
        ]
    )

    for subject in analysis["subjects"]:
        for evaluation in subject["evaluations"]:
            score_by_criterion = {s["criterion_id"]: s["score"] for s in evaluation["scores"]}
            writer.writerow(
                [
                    _escape_formula(project_name),
                    _escape_formula(subject["name"]),
                    _escape_formula(evaluation["scorer_name"]),
                    _escape_formula(_official_label(evaluation["official_included"])),
                    *[
                        _escape_formula(score_by_criterion.get(c["id"], ""))
                        for c in criteria
                    ],
                    _escape_formula(evaluation["total"]),
                    _escape_formula(evaluation["feedback"]),
                    _escape_formula(_format_timestamp(evaluation["submitted_at"])),
                ]
            )

    return UTF8_BOM + buffer.getvalue()


def build_markdown(analysis: dict) -> str:
    project = analysis["project"]
    lines: list[str] = []

    lines.append(f"# {project['name']}")
    lines.append("")
    mode_label = "発表者ごとに採点・発表" if project["presentation_mode"] == "SEQUENTIAL" else "全発表者終了後にまとめて発表"
    lines.append(f"- 状態: {project['status']}")
    lines.append(f"- 結果発表方式: {mode_label}")
    lines.append(f"- 公式集計対象の採点者: {analysis['official_scorer_count']}名")
    lines.append(f"- 公式集計対象外の採点者: {analysis['excluded_scorer_count']}名")
    lines.append(f"- 採点者1人あたりの満点: {analysis['theoretical_max_total']}点")
    lines.append("")

    lines.append("## 最終ランキング")
    lines.append("")
    lines.append("| 順位 | 被採点者 | 合計 | 平均 |")
    lines.append("| --- | --- | --- | --- |")
    for subject in analysis["subjects"]:
        lines.append(
            f"| {subject['rank']} | {subject['name']} | "
            f"{subject['total_score']} | {subject['mean_score']} |"
        )
    lines.append("")

    for subject in analysis["subjects"]:
        lines.append(f"## {subject['name']}")
        lines.append("")
        lines.append(
            f"{subject['rank']}位 / 合計 {subject['total_score']}点 / "
            f"平均 {subject['mean_score']}点 / 公式集計対象 {subject['scorer_count']}名"
        )
        lines.append("")

        lines.append("### 採点軸別平均")
        lines.append("")
        lines.append("| 採点軸 | 平均 | 満点 |")
        lines.append("| --- | --- | --- |")
        for average in subject["criterion_averages"]:
            lines.append(
                f"| {average['name']} | {average['average']} | {average['max_score']} |"
            )
        lines.append("")

        lines.append("### 採点者別合計")
        lines.append("")
        lines.append("| 採点者 | 合計 |")
        lines.append("| --- | --- |")
        for judge in subject["judge_totals"]:
            lines.append(f"| {judge['display_name']} | {judge['total']} |")
        lines.append("")

        lines.append("### フィードバック")
        lines.append("")
        if not subject["evaluations"]:
            lines.append("(提出されたフィードバックはありません)")
            lines.append("")
        for evaluation in subject["evaluations"]:
            label = _official_label(evaluation["official_included"])
            feedback = evaluation["feedback"].strip() or "(記入なし)"
            lines.append(
                f"- **{evaluation['scorer_name']}**"
                f"({evaluation['total']}点 / 公式集計{label}): {feedback}"
            )
        lines.append("")

    return "\n".join(lines)
