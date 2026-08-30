"""画面(Jinja2)ルート。認可の実体は各APIエンドポイント側で行い、画面
ルート自体はどのユーザーでも開ける(未ログイン/権限不足時はJSがAPIの
403を検知してログイン画面等へ誘導する)。
"""

from __future__ import annotations

from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/")
def home():
    return render_template("home.html")


@pages_bp.get("/projects/new")
def project_create():
    return render_template("project_create.html")


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


@pages_bp.get("/host/<int:project_id>/present")
def result_presentation_page(project_id: int):
    return render_template("result_presentation.html", project_id=project_id)


@pages_bp.get("/scorer")
def scorer_dashboard_page():
    return render_template("scorer_dashboard.html")


@pages_bp.get("/scorer/subjects/<int:subject_id>")
def scoring_page(subject_id: int):
    return render_template("scoring.html", subject_id=subject_id)
