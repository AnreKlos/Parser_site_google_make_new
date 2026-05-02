"""baseline: initial schema (Lead + AuditLog)

Revision ID: e569ffe55199
Revises: 
Create Date: 2026-05-02 21:25:13.055733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e569ffe55199'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("google_rating", sa.Float(), nullable=True),
        sa.Column("reviews_count", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("google_maps_url", sa.String(length=500), nullable=True),
        sa.Column("emails", sa.Text(), nullable=True),
        sa.Column("social_links", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'new'")),
        sa.Column("tech_score", sa.Integer(), nullable=True),
        sa.Column("load_time_sec", sa.Float(), nullable=True),
        sa.Column("audit_notes", sa.Text(), nullable=True),
        sa.Column("pitch_text", sa.Text(), nullable=True),
        sa.Column("raw_reviews", sa.Text(), nullable=True),
        sa.Column("site_config_path", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True, server_default=sa.text("'other'")),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("website"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_lead_id"), "audit_logs", ["lead_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_audit_logs_lead_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("leads")
