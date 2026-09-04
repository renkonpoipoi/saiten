"""画面(Jinja2)ルート。認可の実体は各APIエンドポイント側で行い、画面
ルート自体はどのユーザーでも開ける(未ログイン/権限不足時はJSがAPIの
403を検知してログイン画面等へ誘導する)。
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, session, url_for

from app.auth.decorators import require_host
from app.errors import ConflictError, NotFoundError
from app.extensions import db
from app.models import Project, Scorer

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/")
def home():
    return render_template("home.html")


@pages_bp.get("/projects/new")
def project_create():
    return render_template("project_create.html")


@pages_bp.get("/projects/<int:project_id>/created")
def project_created_page(project_id: int):
    """作成完了画面のURL(project_create.jsがpushStateで書き換える先)。

    リロードや共有で直接開かれても404にしないためのルート。
    Host Code / 参加コードはDBにhashしか無く再表示できないため、ここでは
    コードを一切描画せず、Host Dashboardへの導線だけを出す。
    """
    return render_template("project_created.html", project_id=project_id)


@pages_bp.get("/host/login")
def host_login_page():
    return render_template("host_login.html")


@pages_bp.get("/join")
def join_page():
    return render_template("join.html")


@pages_bp.get("/host/<int:project_id>/settings")
def host_settings_page(project_id: int):
    return render_template("host_settings.html", project_id=project_id)


@pages_bp.get("/host/<int:project_id>")
def host_dashboard_page(project_id: int):
    return render_template("host_dashboard.html", project_id=project_id)


@pages_bp.get("/host/<int:project_id>/analysis")
def host_analysis_page(project_id: int):
    return render_template("host_analysis.html", project_id=project_id)


@pages_bp.get("/host/<int:project_id>/present")
def result_presentation_page(project_id: int):
    return render_template("result_presentation.html", project_id=project_id)


@pages_bp.post("/host/<int:project_id>/scoring")
@require_host
def host_scoring_entry(project_id: int):
    """Host本人が、コード入力なしに自分の採点画面を新規タブで開くための入口。

    Host Dashboard 上の通常のHTML form (method="post" target="_blank") から
    呼ばれる。session設定とredirectをサーバー側で完結させるので、
    クライアントは about:blank を開いて WindowProxy を操作する必要がない。

    対象のScorerは **サーバーが project_id + is_host_scorer + is_active から
    決める。** clientからscorer_idを受け取らないので、この経路で他のScorerへ
    なりすますことはできない。

    POSTのみ。GETでsessionを書き換えないことで、リンクのプリフェッチや
    <img src> のような受動的なrequestでscorer sessionが差し替わることを防ぐ。
    既存のCSRFProtectの保護下に入る(formがcsrf_tokenを送る)。

    Host sessionは破棄しない。session["host_project_id"] と
    session["scorer_id"] は別keyなので、元タブのHost Dashboardは
    そのまま使い続けられる。
    """
    project = db.session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")

    scorer = (
        Scorer.query.filter_by(
            project_id=project.id, is_host_scorer=True, is_active=True
        )
        .order_by(Scorer.id)
        .first()
    )
    if scorer is None:
        raise ConflictError(
            "This project has no host scorer. Assign one in the project settings."
        )

    session["scorer_id"] = scorer.id
    session["scorer_project_id"] = scorer.project_id
    # 303: POSTの結果をGETで取りに行かせる(再読み込みでPOSTが再送されない)
    return redirect(url_for("pages.scorer_dashboard_page"), code=303)


@pages_bp.get("/scorer")
def scorer_dashboard_page():
    return render_template("scorer_dashboard.html")


@pages_bp.get("/scorer/subjects/<int:subject_id>")
def scoring_page(subject_id: int):
    return render_template("scoring.html", subject_id=subject_id)
