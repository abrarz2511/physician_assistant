"""Add encounter artifact storage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260706_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "encounters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_session_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('streaming', 'completed', 'disconnected', 'failed')",
            name="ck_encounters_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_encounters_created_at", "encounters", ["created_at"])
    op.create_index("ix_encounters_external_session_id", "encounters", ["external_session_id"])

    op.create_table(
        "soap_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("encounter_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("encounter_id"),
    )
    op.create_index("ix_soap_notes_encounter_id", "soap_notes", ["encounter_id"])

    op.create_table(
        "coding_recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("encounter_id", sa.Uuid(), nullable=False),
        sa.Column("setting", sa.String(length=255), nullable=False),
        sa.Column("patient_type", sa.String(length=255), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("documentation_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("encounter_id"),
    )
    op.create_index(
        "ix_coding_recommendations_encounter_id",
        "coding_recommendations",
        ["encounter_id"],
    )


def downgrade() -> None:
    op.drop_table("coding_recommendations")
    op.drop_table("soap_notes")
    op.drop_table("encounters")
