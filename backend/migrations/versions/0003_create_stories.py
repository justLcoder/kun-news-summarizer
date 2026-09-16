"""Add stories and article membership."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stories",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stories"),
        sa.CheckConstraint(
            "first_published_at <= last_published_at",
            name="ck_stories_publication_bounds",
        ),
    )
    op.create_index(
        "ix_stories_last_published_at_id",
        "stories",
        [sa.text("last_published_at DESC"), sa.text("id DESC")],
        unique=False,
    )

    op.create_table(
        "story_articles",
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("story_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("article_id", name="pk_story_articles"),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="fk_story_articles_article_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["story_id"],
            ["stories.id"],
            name="fk_story_articles_story_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_story_articles_story_id",
        "story_articles",
        ["story_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_story_articles_story_id", table_name="story_articles")
    op.drop_table("story_articles")
    op.drop_index("ix_stories_last_published_at_id", table_name="stories")
    op.drop_table("stories")
