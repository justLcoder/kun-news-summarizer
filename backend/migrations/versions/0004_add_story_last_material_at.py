"""Track the latest material development for candidate retrieval."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "stories",
        sa.Column("last_material_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing Story rows predate materiality decisions. Their latest known
    # publication is the only deterministic safe value available for backfill.
    op.execute(
        "UPDATE stories SET last_material_at = last_published_at "
        "WHERE last_material_at IS NULL"
    )
    op.alter_column("stories", "last_material_at", nullable=False)
    op.create_index(
        "ix_stories_last_material_at_id",
        "stories",
        [sa.text("last_material_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_stories_last_material_at_id", table_name="stories")
    op.drop_column("stories", "last_material_at")
