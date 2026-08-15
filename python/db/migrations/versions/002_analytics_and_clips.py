"""Add session_id / top_score / referrer_clip_slug to search_history;
user_id / session_id / clicked_timestamp to search_clicks;
shared_clips table.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extend search_history ─────────────────────────────────────────────────
    op.add_column("search_history", sa.Column("session_id", sa.Text(), nullable=True))
    op.add_column("search_history", sa.Column("top_score", sa.Float(), nullable=True))
    op.add_column("search_history", sa.Column("referrer_clip_slug", sa.Text(), nullable=True))
    op.create_index("ix_search_history_session_id", "search_history", ["session_id"])
    op.create_index("ix_search_history_created_at", "search_history", ["created_at"])

    # ── Extend search_clicks ──────────────────────────────────────────────────
    op.add_column("search_clicks", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_search_clicks_user_id",
        "search_clicks",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("search_clicks", sa.Column("session_id", sa.Text(), nullable=True))
    op.add_column("search_clicks", sa.Column("clicked_timestamp", sa.Float(), nullable=True))
    op.create_index("ix_search_clicks_search_id", "search_clicks", ["search_id"])

    # ── New table: shared_clips ───────────────────────────────────────────────
    op.create_table(
        "shared_clips",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column(
            "video_id",
            sa.Integer(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("transcript_text", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("slug", name="uq_shared_clips_slug"),
    )
    op.create_index("ix_shared_clips_slug", "shared_clips", ["slug"])
    op.create_index("ix_shared_clips_video_id", "shared_clips", ["video_id"])
    op.create_index("ix_shared_clips_created_at", "shared_clips", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_shared_clips_created_at", table_name="shared_clips")
    op.drop_index("ix_shared_clips_video_id", table_name="shared_clips")
    op.drop_index("ix_shared_clips_slug", table_name="shared_clips")
    op.drop_table("shared_clips")

    op.drop_index("ix_search_clicks_search_id", table_name="search_clicks")
    op.drop_column("search_clicks", "clicked_timestamp")
    op.drop_column("search_clicks", "session_id")
    op.drop_constraint("fk_search_clicks_user_id", "search_clicks", type_="foreignkey")
    op.drop_column("search_clicks", "user_id")

    op.drop_index("ix_search_history_created_at", table_name="search_history")
    op.drop_index("ix_search_history_session_id", table_name="search_history")
    op.drop_column("search_history", "referrer_clip_slug")
    op.drop_column("search_history", "top_score")
    op.drop_column("search_history", "session_id")
