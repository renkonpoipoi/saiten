"""enforce one host scorer per project

Phase 10A: 「1 Projectにつき is_host_scorer=true は最大1人」を DB 制約で固める。

設計方針:

- **expand only**。既存カラムのADD/DROP/型変更/DEFAULT変更を一切行わない。
  追加するのは部分UNIQUE INDEXだけで、テーブル再構築(batch_alter_table)は
  行わない。CREATE INDEX / DROP INDEX はSQLiteでもPostgreSQLでも
  ALTER TABLE を必要としないため、既存migrationの方針をそのまま守れる。
- **既存行へのUPDATEを一切発行しない。** Phase 10A以前に作られたProjectも
  「is_host_scorerが立ったScorerは高々1人」という状態で作られているため、
  そのままindexを張れる(合成された「ホスト」Scorerも1人だけ)。
  本番(Neon)の既存データへ書き込まないことでリスクを最小化する。
- 部分INDEXはPostgreSQLとSQLiteの双方が対応しており、同一のDDL
  (CREATE UNIQUE INDEX ... ON scorers (project_id) WHERE is_host_scorer)
  が生成される。offline (`--sql`) 生成もreflectionを必要としない。
- WHERE句にはBoolean列をそのまま置く。integer literal (= 1) との比較を
  書かないのは、Phase 7Aで問題になった「SQLite上のautogenerateがBOOLEANを
  integerに固定してしまう」罠と同じ種類のdialect差を持ち込まないため。

この制約により、Host roleの付け替え(project_service.set_host_scorer)は
「旧Hostを降ろす -> flush -> 新Hostを立てる」の順序を守る必要がある。
plainなunique indexはdeferrableではないため、同一flush内で2人trueになると
即座に違反する。

Revision ID: c1f7a04b9e26
Revises: 9c4e17a2b8d3
Create Date: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1f7a04b9e26'
down_revision = '9c4e17a2b8d3'
branch_labels = None
depends_on = None

INDEX_NAME = 'uq_scorers_one_host_per_project'


def upgrade():
    op.create_index(
        INDEX_NAME,
        'scorers',
        ['project_id'],
        unique=True,
        postgresql_where=sa.text('is_host_scorer'),
        sqlite_where=sa.text('is_host_scorer'),
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name='scorers')
