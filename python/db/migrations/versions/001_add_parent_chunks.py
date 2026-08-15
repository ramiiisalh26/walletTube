"""Add transcript_parent_chunks table and parent_chunk_id FK on transcript_chunks.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── New table: transcript_parent_chunks ───────────────────────────────────
    op.create_table(
        "transcript_parent_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "transcript_id",
            sa.Integer(),
            sa.ForeignKey("transcripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "video_id",
            sa.Integer(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_time", sa.Numeric(10, 3), nullable=False),
        sa.Column("end_time", sa.Numeric(10, 3), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "transcript_id", "chunk_index", name="uq_parent_chunks_transcript_idx"
        ),
    )
    op.create_index(
        "ix_parent_chunks_transcript_id",
        "transcript_parent_chunks",
        ["transcript_id"],
    )
    op.create_index(
        "ix_parent_chunks_video_id",
        "transcript_parent_chunks",
        ["video_id"],
    )

    # ── Alter transcript_chunks: add nullable FK to parent ────────────────────
    # Nullable so existing rows are valid without a backfill migration.
    # Run scripts/backfill_parent_chunks.py after deploying to populate old rows.
    op.add_column(
        "transcript_chunks",
        sa.Column("parent_chunk_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tc_parent_chunk",
        "transcript_chunks",
        "transcript_parent_chunks",
        ["parent_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tc_parent_chunk_id", "transcript_chunks", ["parent_chunk_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tc_parent_chunk_id", table_name="transcript_chunks")
    op.drop_constraint("fk_tc_parent_chunk", "transcript_chunks", type_="foreignkey")
    op.drop_column("transcript_chunks", "parent_chunk_id")
    op.drop_index("ix_parent_chunks_video_id", table_name="transcript_parent_chunks")
    op.drop_index(
        "ix_parent_chunks_transcript_id", table_name="transcript_parent_chunks"
    )
    op.drop_table("transcript_parent_chunks")
