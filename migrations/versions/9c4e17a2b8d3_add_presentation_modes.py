"""add presentation modes

Phase 8: 結果発表方式(BATCH / SEQUENTIAL)とSubject単位の進行状態を追加する。

設計方針:

- **expand only**。ADD COLUMN のみで、既存カラムのDROP/RENAME/型変更/DEFAULT変更は
  一切行わない。initial migration (b37d61517847) は変更しない。
- 既存行への明示的なUPDATEを発行しない。server_defaultにより、Phase 8以前に作られた
  Projectは自動的に presentation_mode='BATCH'、Subjectは presentation_status='WAITING'
  になる。本番(Neon)の既存データへ書き込みを行わないことでリスクを最小化する。
- **CHECK制約はcolumn levelのinline指定で付与する。** SQLiteは
  ALTER TABLE ... ADD CONSTRAINT を持たないが、ADD COLUMN 時のinline column
  constraintは受け付ける。SQLAlchemyのCreateColumnコンパイラはSQLite/PostgreSQLで
  同一のDDL断片を出力するため、batch_alter_table(テーブル再構築)を使わずに
  両dialectで同じ名前付きCHECK制約を持たせられる。offline (`--sql`) 生成も
  reflectionを必要としないため両dialectで動作する。
- Boolean列を追加していない。Phase 7Aで問題になった
  「SQLite上のautogenerateがBOOLEAN DEFAULTをinteger literalに固定してしまう」罠を
  構造的に回避するため、モード/状態はString + CHECKで表現している。

Revision ID: 9c4e17a2b8d3
Revises: b37d61517847
Create Date: 2026-09-03

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c4e17a2b8d3'
down_revision = 'b37d61517847'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'projects',
        sa.Column(
            'presentation_mode',
            sa.String(length=16),
            sa.CheckConstraint(
                "presentation_mode IN ('BATCH','SEQUENTIAL')",
                name='ck_projects_presentation_mode',
            ),
            server_default=sa.text("'BATCH'"),
            nullable=False,
        ),
    )
    op.add_column(
        'subjects',
        sa.Column(
            'presentation_status',
            sa.String(length=16),
            sa.CheckConstraint(
                "presentation_status IN ('WAITING','SCORING','LOCKED','PRESENTED')",
                name='ck_subjects_presentation_status',
            ),
            server_default=sa.text("'WAITING'"),
            nullable=False,
        ),
    )
    # SQLiteのADD COLUMNは非定数DEFAULT(CURRENT_TIMESTAMP等)を許さないため、
    # この2列はnullable / DEFAULT無しにしてある。
    op.add_column('subjects', sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('subjects', sa.Column('presented_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    # CHECK制約はカラムに紐づくinline制約なので、DROP COLUMNで一緒に除去される
    # (PostgreSQL、およびSQLite 3.35.5+ の双方で確認済み)。
    op.drop_column('subjects', 'presented_at')
    op.drop_column('subjects', 'locked_at')
    op.drop_column('subjects', 'presentation_status')
    op.drop_column('projects', 'presentation_mode')
