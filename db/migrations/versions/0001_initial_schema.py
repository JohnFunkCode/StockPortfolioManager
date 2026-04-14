"""Initial schema — tenants, users, portfolio, watchlist, agents, harvester, dedup config

Revision ID: 0001
Revises:
Create Date: 2026-04-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, TEXT

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extensions ────────────────────────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ── Tenants ───────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("discord_webhook_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── Users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("google_sub", sa.Text, nullable=False, unique=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_check_constraint("ck_users_role", "users", "role IN ('admin', 'analyst', 'viewer')")

    # ── Tenant Config ─────────────────────────────────────────────────────────
    # Stores per-tenant tunable values: conviction threshold, puts_budget,
    # dedup windows per alert type, etc.
    op.create_table(
        "tenant_config",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("conviction_threshold", sa.Integer, nullable=False, server_default="4"),
        sa.Column("puts_budget", sa.Numeric(12, 2), nullable=False),
        sa.Column("scanner_scope", sa.Text, nullable=False, server_default="'portfolio'"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_check_constraint(
        "ck_tenant_config_scanner_scope", "tenant_config",
        "scanner_scope IN ('portfolio', 'watchlist')"
    )

    # ── Alert Dedup Config ────────────────────────────────────────────────────
    # One row per (tenant, alert_type) — suppression window configurable
    # without a deployment.
    op.create_table(
        "alert_dedup_config",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("suppress_minutes", sa.Integer, nullable=False),
    )
    op.create_unique_constraint("uq_alert_dedup_config_tenant_type", "alert_dedup_config", ["tenant_id", "alert_type"])

    # ── Portfolio Positions ───────────────────────────────────────────────────
    op.create_table(
        "positions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("purchase_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
        sa.Column("purchase_date", sa.Date, nullable=True),
        sa.Column("currency", sa.Text, nullable=False, server_default="'USD'"),
        sa.Column("sale_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("sale_date", sa.Date, nullable=True),
        sa.Column("current_price", sa.Numeric(12, 4), nullable=True),
    )
    op.create_index("ix_positions_tenant_symbol", "positions", ["tenant_id", "symbol"])

    # ── Watchlist ─────────────────────────────────────────────────────────────
    op.create_table(
        "watchlist",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("currency", sa.Text, nullable=False, server_default="'USD'"),
        sa.Column("tags", ARRAY(TEXT), nullable=True),
    )
    op.create_index("ix_watchlist_tenant_symbol", "watchlist", ["tenant_id", "symbol"])

    # ── Agent Signals ─────────────────────────────────────────────────────────
    op.create_table(
        "agent_signals",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("triggers", JSONB, nullable=True),
        sa.Column("escalated", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("fired_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_check_constraint("ck_signals_direction", "agent_signals", "direction IN ('buy', 'sell', 'neutral')")
    op.create_index("ix_agent_signals_tenant_fired", "agent_signals", ["tenant_id", "fired_at"])

    # ── Agent Recommendations ─────────────────────────────────────────────────
    op.create_table(
        "agent_recommendations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("recommendation", sa.Text, nullable=False),
        sa.Column("conviction", sa.Text, nullable=False),
        sa.Column("entry_low", sa.Numeric(12, 4), nullable=True),
        sa.Column("entry_high", sa.Numeric(12, 4), nullable=True),
        sa.Column("price_target", sa.Numeric(12, 4), nullable=True),
        sa.Column("stop_loss", sa.Numeric(12, 4), nullable=True),
        sa.Column("details", JSONB, nullable=True),
        sa.Column("fired_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_check_constraint(
        "ck_recommendations_recommendation", "agent_recommendations",
        "recommendation IN ('BUY', 'SELL', 'HOLD', 'AVOID')"
    )
    op.create_check_constraint(
        "ck_recommendations_conviction", "agent_recommendations",
        "conviction IN ('HIGH', 'MEDIUM', 'LOW')"
    )
    op.create_index("ix_agent_recommendations_tenant_fired", "agent_recommendations", ["tenant_id", "fired_at"])

    # ── Alert Dedup State ─────────────────────────────────────────────────────
    op.create_table(
        "alert_dedup",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("fired_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index(
        "ix_alert_dedup_lookup",
        "alert_dedup",
        ["tenant_id", "symbol", "alert_type", "fired_at"],
    )

    # ── Harvester Plan Templates ───────────────────────────────────────────────
    op.create_table(
        "harvester_plan_templates",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("config", JSONB, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── Harvester Plan Rungs ──────────────────────────────────────────────────
    op.create_table(
        "harvester_plan_rungs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("template_id", UUID, sa.ForeignKey("harvester_plan_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price_target", sa.Numeric(12, 4), nullable=False),
        sa.Column("shares_to_sell", sa.Numeric(12, 4), nullable=False),
        sa.Column("hit", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("hit_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ── Row-Level Security ────────────────────────────────────────────────────
    for table in [
        "positions", "watchlist", "agent_signals", "agent_recommendations",
        "alert_dedup", "alert_dedup_config", "tenant_config",
        "harvester_plan_templates",
    ]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true)::UUID)
        """)

    # ── Default Dedup Windows (system-wide seed, applied per tenant on onboarding) ──
    # These are the agreed defaults from the decisions log.
    # Stored as minutes: 2h=120, 4h=240, 24h=1440, 12h=720
    op.execute("""
        CREATE OR REPLACE FUNCTION seed_tenant_defaults(p_tenant_id UUID) RETURNS void AS $$
        BEGIN
            INSERT INTO alert_dedup_config (tenant_id, alert_type, suppress_minutes) VALUES
                (p_tenant_id, 'signal_buy',              120),
                (p_tenant_id, 'signal_sell',             120),
                (p_tenant_id, 'recommendation_buy',      240),
                (p_tenant_id, 'recommendation_sell',     240),
                (p_tenant_id, 'recommendation_hold',    1440),
                (p_tenant_id, 'portfolio_at_risk',       120),
                (p_tenant_id, 'portfolio_inst_exit',     120),
                (p_tenant_id, 'portfolio_report',        720);
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS seed_tenant_defaults(UUID)")

    for table in [
        "positions", "watchlist", "agent_signals", "agent_recommendations",
        "alert_dedup", "alert_dedup_config", "tenant_config",
        "harvester_plan_templates",
    ]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.drop_table("harvester_plan_rungs")
    op.drop_table("harvester_plan_templates")
    op.drop_table("alert_dedup")
    op.drop_table("alert_dedup_config")
    op.drop_table("agent_recommendations")
    op.drop_table("agent_signals")
    op.drop_table("watchlist")
    op.drop_table("positions")
    op.drop_table("tenant_config")
    op.drop_table("users")
    op.drop_table("tenants")
    op.execute('DROP EXTENSION IF EXISTS "pgcrypto"')
