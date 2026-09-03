"""モデル一式をここでまとめてimportする。

Flask-Migrateのautogenerateやテストのメタデータ作成(db.create_all())が
全テーブルを検出できるように、パッケージimport時に全モジュールを読み込む。
"""

from app.models.project import Project, PRESENTATION_MODES, PROJECT_STATUSES  # noqa: F401
from app.models.subject import Subject, SUBJECT_PRESENTATION_STATUSES  # noqa: F401
from app.models.criterion import Criterion  # noqa: F401
from app.models.scorer import Scorer  # noqa: F401
from app.models.evaluation import Evaluation, EvaluationScore, EVALUATION_STATUSES  # noqa: F401

__all__ = [
    "Project",
    "PROJECT_STATUSES",
    "PRESENTATION_MODES",
    "Subject",
    "SUBJECT_PRESENTATION_STATUSES",
    "Criterion",
    "Scorer",
    "Evaluation",
    "EvaluationScore",
    "EVALUATION_STATUSES",
]
