"""API/画面ルートに付与する権限チェックデコレータ。

権限チェックは常にサーバー側で完結させ、フロントの表示制御(ボタンdisabled等)
はUXのためだけに存在させる(実装計画 v1 10節)。

Host権限は session["host_project_id"] 起因、Scorer権限は session["scorer_id"]
起因でそれぞれ独立に判定し、Scorer.is_host_scorer(表示用フラグ)は権限判定に
一切使わない(実装計画 v2 11節)。
"""

from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import session

from app.errors import ForbiddenError


def require_host(view_func: Callable) -> Callable:
    """URLの project_id と session["host_project_id"] の一致を要求する。

    project_id はルートの<int:project_id>から渡される想定。
    """

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        project_id = kwargs.get("project_id")
        if project_id is None:
            raise ForbiddenError("project_id is required for host authorization.")
        if session.get("host_project_id") != project_id:
            raise ForbiddenError("Host session required for this project.")
        return view_func(*args, **kwargs)

    return wrapper


def require_scorer(view_func: Callable) -> Callable:
    """Scorerセッション(session["scorer_id"])を持つことを要求する。

    採点対象(evaluation等)がそのScorer本人のものであるかの照合は、
    呼び出し先のroute/service側で個別に行う(cross-project/他者データへの
    アクセスを防ぐため)。
    """

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("scorer_id"):
            raise ForbiddenError("Scorer session required.")
        return view_func(*args, **kwargs)

    return wrapper


def current_scorer_id() -> int | None:
    return session.get("scorer_id")


def current_scorer_project_id() -> int | None:
    return session.get("scorer_project_id")


def current_host_project_id() -> int | None:
    return session.get("host_project_id")
