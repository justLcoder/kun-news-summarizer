"""Create the initial articles table."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_articles"),
        sa.UniqueConstraint("source_url", name="uq_articles_source_url"),
        sa.CheckConstraint("title ~ '[^[:space:]]'", name="ck_articles_title_nonblank"),
        sa.CheckConstraint("content ~ '[^[:space:]]'", name="ck_articles_content_nonblank"),
        comment="Successfully retrieved source articles",
    )


def downgrade():
    op.drop_table("articles")
