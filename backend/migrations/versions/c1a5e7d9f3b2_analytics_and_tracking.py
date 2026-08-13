"""visitor/traffic analytics + tracking links + acquisition attribution

Revision ID: c1a5e7d9f3b2
Revises: a41d7e9c3b20
Create Date: 2026-08-13 10:00:00.000000

Ziyaretci/trafik analitigi (yalnizca platform yoneticisi gorur) icin sema:
`analytics_visitors`, `analytics_sessions`, `analytics_events` (+ kontrollu
`analytics_event_type` enum), yonetici tarafindan olusturulan `tracking_links`,
ve kayit aninda denormalize edilen `user_acquisition_attribution` /
`organization_acquisition_attribution`. Tum kullanici/organizasyon FK'leri, o
satirlar silinse bile analitik satirlarinin bozulmamasi icin SET NULL/CASCADE
kurallarina baglidir (bkz. app.models.analytics).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1a5e7d9f3b2"
down_revision: str | Sequence[str] | None = "a41d7e9c3b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    analytics_event_type = sa.Enum(
        "PAGE_VIEW",
        "VISITOR_SESSION_STARTED",
        "SIGNUP_STARTED",
        "SIGNUP_COMPLETED",
        "LOGIN_SUCCEEDED",
        "LOGIN_FAILED_SECURITY_SUMMARY",
        "LOGOUT",
        "ORGANIZATION_CREATED",
        "FIRST_PROJECT_CREATED",
        "FIRST_TEST_STARTED",
        "FIRST_TEST_COMPLETED",
        name="analytics_event_type",
    )

    # --- analytics_visitors ---
    op.create_table(
        "analytics_visitors",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_utm_source", sa.String(length=200), nullable=True),
        sa.Column("first_utm_medium", sa.String(length=200), nullable=True),
        sa.Column("first_utm_campaign", sa.String(length=200), nullable=True),
        sa.Column("first_utm_content", sa.String(length=200), nullable=True),
        sa.Column("first_utm_term", sa.String(length=200), nullable=True),
        sa.Column("first_referral_code", sa.String(length=100), nullable=True),
        sa.Column("first_referrer_domain", sa.String(length=255), nullable=True),
        sa.Column("first_landing_path", sa.String(length=1024), nullable=True),
        sa.Column("device_category", sa.String(length=20), nullable=True),
        sa.Column("browser_family", sa.String(length=50), nullable=True),
        sa.Column("os_family", sa.String(length=50), nullable=True),
        sa.Column("linked_user_id", sa.Uuid(), nullable=True),
        sa.Column("linked_organization_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["linked_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["linked_organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analytics_visitors_last_seen_at", "analytics_visitors", ["last_seen_at"], unique=False
    )

    # --- analytics_sessions ---
    op.create_table(
        "analytics_sessions",
        sa.Column("visitor_id", sa.Uuid(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("utm_source", sa.String(length=200), nullable=True),
        sa.Column("utm_medium", sa.String(length=200), nullable=True),
        sa.Column("utm_campaign", sa.String(length=200), nullable=True),
        sa.Column("utm_content", sa.String(length=200), nullable=True),
        sa.Column("utm_term", sa.String(length=200), nullable=True),
        sa.Column("referral_code", sa.String(length=100), nullable=True),
        sa.Column("referrer_domain", sa.String(length=255), nullable=True),
        sa.Column("landing_path", sa.String(length=1024), nullable=True),
        sa.Column("device_category", sa.String(length=20), nullable=True),
        sa.Column("browser_family", sa.String(length=50), nullable=True),
        sa.Column("os_family", sa.String(length=50), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["visitor_id"], ["analytics_visitors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analytics_sessions_visitor_id", "analytics_sessions", ["visitor_id"], unique=False
    )
    op.create_index(
        "ix_analytics_sessions_last_event_at", "analytics_sessions", ["last_event_at"], unique=False
    )

    # --- analytics_events (append-only) ---
    op.create_table(
        "analytics_events",
        sa.Column("event_type", analytics_event_type, nullable=False),
        sa.Column("visitor_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=True),
        sa.Column("referrer_domain", sa.String(length=255), nullable=True),
        sa.Column("utm_source", sa.String(length=200), nullable=True),
        sa.Column("utm_medium", sa.String(length=200), nullable=True),
        sa.Column("utm_campaign", sa.String(length=200), nullable=True),
        sa.Column("utm_content", sa.String(length=200), nullable=True),
        sa.Column("utm_term", sa.String(length=200), nullable=True),
        sa.Column("referral_code", sa.String(length=100), nullable=True),
        sa.Column("device_category", sa.String(length=20), nullable=True),
        sa.Column("browser_family", sa.String(length=50), nullable=True),
        sa.Column("os_family", sa.String(length=50), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("dedup_key", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["visitor_id"], ["analytics_visitors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["analytics_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_analytics_events_dedup_key"),
    )
    op.create_index("ix_analytics_events_created_at", "analytics_events", ["created_at"], unique=False)
    op.create_index(
        "ix_analytics_events_event_type_created_at",
        "analytics_events",
        ["event_type", "created_at"],
        unique=False,
    )
    op.create_index("ix_analytics_events_visitor_id", "analytics_events", ["visitor_id"], unique=False)
    op.create_index("ix_analytics_events_session_id", "analytics_events", ["session_id"], unique=False)
    op.create_index("ix_analytics_events_user_id", "analytics_events", ["user_id"], unique=False)
    op.create_index(
        "ix_analytics_events_organization_id", "analytics_events", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_analytics_events_referral_code", "analytics_events", ["referral_code"], unique=False
    )
    op.create_index(
        "ix_analytics_events_utm_campaign", "analytics_events", ["utm_campaign"], unique=False
    )

    # --- tracking_links ---
    op.create_table(
        "tracking_links",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("destination_path", sa.String(length=1024), nullable=False),
        sa.Column("utm_source", sa.String(length=200), nullable=True),
        sa.Column("utm_medium", sa.String(length=200), nullable=True),
        sa.Column("utm_campaign", sa.String(length=200), nullable=True),
        sa.Column("utm_content", sa.String(length=200), nullable=True),
        sa.Column("referral_code", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referral_code", name="uq_tracking_links_referral_code"),
    )
    op.alter_column("tracking_links", "is_active", server_default=None)
    op.create_index("ix_tracking_links_created_at", "tracking_links", ["created_at"], unique=False)

    # --- user_acquisition_attribution ---
    op.create_table(
        "user_acquisition_attribution",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.Uuid(), nullable=True),
        sa.Column("first_utm_source", sa.String(length=200), nullable=True),
        sa.Column("first_utm_medium", sa.String(length=200), nullable=True),
        sa.Column("first_utm_campaign", sa.String(length=200), nullable=True),
        sa.Column("first_utm_content", sa.String(length=200), nullable=True),
        sa.Column("first_utm_term", sa.String(length=200), nullable=True),
        sa.Column("first_referral_code", sa.String(length=100), nullable=True),
        sa.Column("first_referrer_domain", sa.String(length=255), nullable=True),
        sa.Column("last_utm_source", sa.String(length=200), nullable=True),
        sa.Column("last_utm_medium", sa.String(length=200), nullable=True),
        sa.Column("last_utm_campaign", sa.String(length=200), nullable=True),
        sa.Column("last_utm_content", sa.String(length=200), nullable=True),
        sa.Column("last_utm_term", sa.String(length=200), nullable=True),
        sa.Column("last_referral_code", sa.String(length=100), nullable=True),
        sa.Column("last_referrer_domain", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["visitor_id"], ["analytics_visitors.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_acquisition_attribution_user_id"),
    )
    op.create_index(
        "ix_user_acquisition_attribution_first_campaign",
        "user_acquisition_attribution",
        ["first_utm_campaign"],
        unique=False,
    )
    op.create_index(
        "ix_user_acquisition_attribution_last_campaign",
        "user_acquisition_attribution",
        ["last_utm_campaign"],
        unique=False,
    )

    # --- organization_acquisition_attribution ---
    op.create_table(
        "organization_acquisition_attribution",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.Uuid(), nullable=True),
        sa.Column("first_utm_source", sa.String(length=200), nullable=True),
        sa.Column("first_utm_medium", sa.String(length=200), nullable=True),
        sa.Column("first_utm_campaign", sa.String(length=200), nullable=True),
        sa.Column("first_utm_content", sa.String(length=200), nullable=True),
        sa.Column("first_utm_term", sa.String(length=200), nullable=True),
        sa.Column("first_referral_code", sa.String(length=100), nullable=True),
        sa.Column("first_referrer_domain", sa.String(length=255), nullable=True),
        sa.Column("last_utm_source", sa.String(length=200), nullable=True),
        sa.Column("last_utm_medium", sa.String(length=200), nullable=True),
        sa.Column("last_utm_campaign", sa.String(length=200), nullable=True),
        sa.Column("last_utm_content", sa.String(length=200), nullable=True),
        sa.Column("last_utm_term", sa.String(length=200), nullable=True),
        sa.Column("last_referral_code", sa.String(length=100), nullable=True),
        sa.Column("last_referrer_domain", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["visitor_id"], ["analytics_visitors.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", name="uq_organization_acquisition_attribution_organization_id"
        ),
    )
    op.create_index(
        "ix_org_acquisition_attribution_first_campaign",
        "organization_acquisition_attribution",
        ["first_utm_campaign"],
        unique=False,
    )
    op.create_index(
        "ix_org_acquisition_attribution_last_campaign",
        "organization_acquisition_attribution",
        ["last_utm_campaign"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_org_acquisition_attribution_last_campaign",
        table_name="organization_acquisition_attribution",
    )
    op.drop_index(
        "ix_org_acquisition_attribution_first_campaign",
        table_name="organization_acquisition_attribution",
    )
    op.drop_table("organization_acquisition_attribution")

    op.drop_index(
        "ix_user_acquisition_attribution_last_campaign", table_name="user_acquisition_attribution"
    )
    op.drop_index(
        "ix_user_acquisition_attribution_first_campaign", table_name="user_acquisition_attribution"
    )
    op.drop_table("user_acquisition_attribution")

    op.drop_index("ix_tracking_links_created_at", table_name="tracking_links")
    op.drop_table("tracking_links")

    op.drop_index("ix_analytics_events_utm_campaign", table_name="analytics_events")
    op.drop_index("ix_analytics_events_referral_code", table_name="analytics_events")
    op.drop_index("ix_analytics_events_organization_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_user_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_session_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_visitor_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_event_type_created_at", table_name="analytics_events")
    op.drop_index("ix_analytics_events_created_at", table_name="analytics_events")
    op.drop_table("analytics_events")

    op.drop_index("ix_analytics_sessions_last_event_at", table_name="analytics_sessions")
    op.drop_index("ix_analytics_sessions_visitor_id", table_name="analytics_sessions")
    op.drop_table("analytics_sessions")

    op.drop_index("ix_analytics_visitors_last_seen_at", table_name="analytics_visitors")
    op.drop_table("analytics_visitors")

    sa.Enum(name="analytics_event_type").drop(op.get_bind(), checkfirst=True)
