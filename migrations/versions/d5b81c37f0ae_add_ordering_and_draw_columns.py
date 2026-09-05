"""add ordering and draw columns

Phase 10B: 発表順の手動並び替えとランダム抽選のための列を追加する。

追加するもの:

- projects.subject_order_mode  MANUAL / RANDOM_DRAW
- projects.draw_cursor         公開済みの抽選件数(compare-and-swap の対象)
- subjects.draw_order          RANDOM_DRAW時の秘密順。MANUALではNULL
- scorers.sort_order           採点者の並び順(座席順)
- UNIQUE(project_id, draw_order) 秘密順の重複防止

設計方針:

- **expand only**。ADD COLUMN と CREATE INDEX のみで、既存カラムの
  DROP/RENAME/型変更/DEFAULT変更は一切行わない。既存migrationも変更しない。
- **既存行への明示的なUPDATEを発行しない。** server_default により、
  Phase 10B以前に作られたProjectは subject_order_mode='MANUAL' /
  draw_cursor=0 / draw_order=NULL、Scorerは sort_order=0 になる。
  本番(Neon)の既存データへ書き込まないことでリスクを最小化する。
- Scorerの読み出しは (sort_order, id) で行うため、既存Projectは全員0で
  id順にフォールバックし、**従来の作成順と完全に一致する**。backfillは不要。
- CHECK制約はcolumn levelのinline指定で付与する(migration 9c4e17a2b8d3 と
  同じ手法)。SQLiteは ALTER TABLE ... ADD CONSTRAINT を持たないが、
  ADD COLUMN時のinline column constraintは受け付けるため、
  batch_alter_table(テーブル再構築)を使わずに両dialectで同じ制約を持てる。
- 部分INDEXではなく通常の複合UNIQUE INDEX。PostgreSQL / SQLite とも
  NULL は互いに重複とみなされないので、MANUAL(全件NULL)と共存できる。
- Boolean列は追加していない(Phase 7Aの BOOLEAN DEFAULT の罠を回避)。

Revision ID: d5b81c37f0ae
Revises: c1f7a04b9e26
Create Date: 2026-09-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5b81c37f0ae'
down_revision = 'c1f7a04b9e26'
branch_labels = None
depends_on = None

DRAW_INDEX = 'ux_subjects_project_draw'


def upgrade():
    op.add_column(
        'projects',
        sa.Column(
            'subject_order_mode',
            sa.String(length=16),
            sa.CheckConstraint(
                "subject_order_mode IN ('MANUAL','RANDOM_DRAW')",
                name='ck_projects_subject_order_mode',
            ),
            server_default=sa.text("'MANUAL'"),
            nullable=False,
        ),
    )
    op.add_column(
        'projects',
        sa.Column('draw_cursor', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column('subjects', sa.Column('draw_order', sa.Integer(), nullable=True))
    op.add_column(
        'scorers',
        sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.create_index(DRAW_INDEX, 'subjects', ['project_id', 'draw_order'], unique=True)


def downgrade():
    op.drop_index(DRAW_INDEX, table_name='subjects')
    op.drop_column('scorers', 'sort_order')
    op.drop_column('subjects', 'draw_order')
    op.drop_column('projects', 'draw_cursor')
    op.drop_column('projects', 'subject_order_mode')
