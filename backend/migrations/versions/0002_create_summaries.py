"""Store one successful summary per article."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'summaries',
        sa.Column('article_id', sa.BigInteger(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('prompt_version', sa.Text(), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('article_id', name='pk_summaries'),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name='fk_summaries_article_id', ondelete='CASCADE'),
        sa.CheckConstraint("content ~ '[^[:space:]]'", name='ck_summaries_content_nonblank'),
    )


def downgrade():
    op.drop_table('summaries')
