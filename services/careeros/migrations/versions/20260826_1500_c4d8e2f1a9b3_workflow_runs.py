"""workflow runs — step engine with WAIT_FOR_APPROVAL gates (ADR-017)

Revision ID: c4d8e2f1a9b3
Revises: b1c7d0e9a4f2
Create Date: 2026-08-26 15:00:00.000000+00:00

Hand-written: the shared dev database carries other lanes' in-flight tables, so an autogenerate
diff would not be trustworthy. One table, no data migration. Chained off b1c7d0e9a4f2 after
announcing the revision id to the platform lane (their job-provenance revision rebases on this
one, or the other way round — whoever lands second moves down_revision).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d8e2f1a9b3"
down_revision: str | None = "b1c7d0e9a4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_run",
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_ref", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        # Step records and the accumulated context are opaque to SQL — the runner reads and
        # rewrites them whole; nothing queries into them.
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suggestion_id", sa.UUID(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_run")),
    )
    op.create_index(op.f("ix_workflow_run_kind"), "workflow_run", ["kind"], unique=False)
    op.create_index(op.f("ix_workflow_run_state"), "workflow_run", ["state"], unique=False)
    op.create_index(
        op.f("ix_workflow_run_target_ref"), "workflow_run", ["target_ref"], unique=False
    )
    op.create_index(
        op.f("ix_workflow_run_suggestion_id"), "workflow_run", ["suggestion_id"], unique=False
    )
    op.create_index(op.f("ix_workflow_run_user_id"), "workflow_run", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_run_user_id"), table_name="workflow_run")
    op.drop_index(op.f("ix_workflow_run_suggestion_id"), table_name="workflow_run")
    op.drop_index(op.f("ix_workflow_run_target_ref"), table_name="workflow_run")
    op.drop_index(op.f("ix_workflow_run_state"), table_name="workflow_run")
    op.drop_index(op.f("ix_workflow_run_kind"), table_name="workflow_run")
    op.drop_table("workflow_run")
