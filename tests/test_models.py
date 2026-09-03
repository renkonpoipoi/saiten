from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Criterion, Evaluation, EvaluationScore, Project, Scorer, Subject
from app.services.code_service import hash_code


def _make_project(db, *, status="DRAFT"):
    project = Project(
        name="テストプロジェクト",
        status=status,
        host_code_hash=hash_code("host_test-code"),
    )
    db.session.add(project)
    db.session.commit()
    return project


def _make_subject(db, project, sort_order=0):
    subject = Subject(project_id=project.id, name="チームA", sort_order=sort_order)
    db.session.add(subject)
    db.session.commit()
    return subject


def _make_criterion(db, project, sort_order=0):
    criterion = Criterion(
        project_id=project.id, name="独創性", max_score=20, sort_order=sort_order
    )
    db.session.add(criterion)
    db.session.commit()
    return criterion


def _make_scorer(db, project, code="scr_test-code"):
    scorer = Scorer(
        project_id=project.id, display_name="採点者A", access_code_hash=hash_code(code)
    )
    db.session.add(scorer)
    db.session.commit()
    return scorer


def test_primary_keys_autoincrement_on_commit(db):
    """全6モデルについて、commit後にidがNoneではなく自動採番されることを
    明示的に確認する(実装計画レビューの指摘: 従来は間接的にしか確認されて
    いなかった)。
    """
    project = _make_project(db)
    subject = _make_subject(db, project)
    criterion = _make_criterion(db, project)
    scorer = _make_scorer(db, project)

    evaluation = Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
    db.session.add(evaluation)
    db.session.commit()

    score = EvaluationScore(evaluation_id=evaluation.id, criterion_id=criterion.id, score=10)
    db.session.add(score)
    db.session.commit()

    ids = {
        "project": project.id,
        "subject": subject.id,
        "criterion": criterion.id,
        "scorer": scorer.id,
        "evaluation": evaluation.id,
        "evaluation_score": score.id,
    }
    for label, value in ids.items():
        assert value is not None, f"{label}.id should be auto-assigned on commit"
        assert isinstance(value, int) and value > 0, f"{label}.id should be a positive int"


def test_six_models_are_registered_on_metadata(db):
    table_names = set(db.metadata.tables.keys())
    assert table_names == {
        "projects",
        "subjects",
        "criteria",
        "scorers",
        "evaluations",
        "evaluation_scores",
    }


def test_project_status_check_constraint_rejects_unknown_value(db):
    project = Project(name="不正な状態", status="NOT_A_STATUS", host_code_hash=hash_code("h1"))
    db.session.add(project)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_criterion_max_score_must_be_positive(db):
    project = _make_project(db)
    criterion = Criterion(project_id=project.id, name="不正な軸", max_score=0, sort_order=0)
    db.session.add(criterion)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_evaluation_unique_scorer_subject(db):
    project = _make_project(db)
    subject = _make_subject(db, project)
    scorer = _make_scorer(db, project)

    db.session.add(
        Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
    )
    db.session.commit()

    db.session.add(
        Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_evaluation_score_unique_evaluation_criterion(db):
    project = _make_project(db)
    subject = _make_subject(db, project)
    scorer = _make_scorer(db, project)
    criterion = _make_criterion(db, project)

    evaluation = Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
    db.session.add(evaluation)
    db.session.commit()

    db.session.add(
        EvaluationScore(evaluation_id=evaluation.id, criterion_id=criterion.id, score=15)
    )
    db.session.commit()

    db.session.add(
        EvaluationScore(evaluation_id=evaluation.id, criterion_id=criterion.id, score=10)
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_evaluation_score_rejects_negative_score(db):
    project = _make_project(db)
    subject = _make_subject(db, project)
    scorer = _make_scorer(db, project)
    criterion = _make_criterion(db, project)

    evaluation = Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
    db.session.add(evaluation)
    db.session.commit()

    db.session.add(
        EvaluationScore(evaluation_id=evaluation.id, criterion_id=criterion.id, score=-1)
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_criterion_delete_restricted_when_scored(db):
    """採点済みのcriterionは削除できない(ON DELETE RESTRICT)。

    SQLite上でこの制約が効くこと自体が、conftestのPRAGMA foreign_keys=ON
    が機能している証拠にもなる。
    """
    project = _make_project(db)
    subject = _make_subject(db, project)
    scorer = _make_scorer(db, project)
    criterion = _make_criterion(db, project)

    evaluation = Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
    db.session.add(evaluation)
    db.session.commit()
    db.session.add(
        EvaluationScore(evaluation_id=evaluation.id, criterion_id=criterion.id, score=10)
    )
    db.session.commit()

    db.session.delete(criterion)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_deleting_subject_cascades_to_evaluations_and_scores(db):
    project = _make_project(db)
    subject = _make_subject(db, project)
    scorer = _make_scorer(db, project)
    criterion = _make_criterion(db, project)

    evaluation = Evaluation(project_id=project.id, scorer_id=scorer.id, subject_id=subject.id)
    db.session.add(evaluation)
    db.session.commit()
    db.session.add(
        EvaluationScore(evaluation_id=evaluation.id, criterion_id=criterion.id, score=10)
    )
    db.session.commit()
    evaluation_id = evaluation.id

    db.session.delete(subject)
    db.session.commit()

    assert db.session.get(Evaluation, evaluation_id) is None
    assert db.session.query(EvaluationScore).count() == 0


def test_deleting_project_cascades_to_all_children(db):
    project = _make_project(db)
    _make_subject(db, project)
    _make_scorer(db, project)
    _make_criterion(db, project)
    project_id = project.id

    db.session.delete(project)
    db.session.commit()

    assert db.session.query(Subject).filter_by(project_id=project_id).count() == 0
    assert db.session.query(Scorer).filter_by(project_id=project_id).count() == 0
    assert db.session.query(Criterion).filter_by(project_id=project_id).count() == 0


def test_host_code_hash_must_be_unique(db):
    dup_hash = hash_code("host_same-code")
    db.session.add(Project(name="A", status="DRAFT", host_code_hash=dup_hash))
    db.session.commit()

    db.session.add(Project(name="B", status="DRAFT", host_code_hash=dup_hash))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_scorer_access_code_hash_must_be_unique_across_projects(db):
    project_a = _make_project(db)
    project_b = Project(name="別プロジェクト", status="DRAFT", host_code_hash=hash_code("h2"))
    db.session.add(project_b)
    db.session.commit()

    same_code_hash = hash_code("scr_shared-code")
    db.session.add(
        Scorer(project_id=project_a.id, display_name="採点者1", access_code_hash=same_code_hash)
    )
    db.session.commit()

    db.session.add(
        Scorer(project_id=project_b.id, display_name="採点者2", access_code_hash=same_code_hash)
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
