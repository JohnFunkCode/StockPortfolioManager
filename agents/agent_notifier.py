"""
AgentNotifier — multi-tenant, continuous-agent extension of Notifier.

Key differences from the base Notifier:
  - Webhook URL loaded from the tenant's database record (not .env)
  - send_notifications() uses PostgreSQL time-windowed dedup (not notification.log)
  - Adds agent-specific alert methods: send_signal_alert, send_recommendation,
    send_portfolio_alert, send_morning_report, send_heartbeat
"""
from datetime import datetime

import requests
from sqlalchemy import text

from db.database import get_db
from notifier import Notifier


class AgentNotifier(Notifier):
    """
    Subclass of Notifier for use by continuous agents.

    The parent's calculate_and_send_notifications(), check_options_alerts(),
    and check_sentiment_flips() are intentionally not overridden — they remain
    available for main.py's phased retirement plan (Phase 5 decision).
    """

    def __init__(self, tenant_id: str):
        # Skip super().__init__() — no Portfolio object needed for agents.
        self.tenant_id = tenant_id
        self.discord_webhook_url = self._load_webhook()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_webhook(self) -> str | None:
        """Fetch the tenant's Discord webhook URL from the database."""
        with get_db() as conn:
            row = conn.execute(
                text("SELECT discord_webhook_url FROM tenants WHERE id = :tid"),
                {"tid": self.tenant_id},
            ).mappings().fetchone()
        return row["discord_webhook_url"] if row else None

    def _is_suppressed(self, symbol: str, alert_type: str) -> bool:
        """Return True if this (symbol, alert_type) fired within its suppress window."""
        with get_db(tenant_id=self.tenant_id) as conn:
            config = conn.execute(
                text("""
                    SELECT suppress_minutes FROM alert_dedup_config
                    WHERE tenant_id = :tid AND alert_type = :at
                """),
                {"tid": self.tenant_id, "at": alert_type},
            ).fetchone()

            if not config:
                return False

            recent = conn.execute(
                text("""
                    SELECT fired_at FROM alert_dedup
                    WHERE tenant_id = :tid
                      AND symbol     = :sym
                      AND alert_type = :at
                      AND fired_at   > NOW() - (:minutes * INTERVAL '1 minute')
                    ORDER BY fired_at DESC
                    LIMIT 1
                """),
                {"tid": self.tenant_id, "sym": symbol, "at": alert_type, "minutes": config[0]},
            ).fetchone()

        return recent is not None

    def _record_fired(self, symbol: str, alert_type: str) -> None:
        """Insert a dedup record so subsequent calls within the window are suppressed."""
        with get_db(tenant_id=self.tenant_id) as conn:
            conn.execute(
                text("""
                    INSERT INTO alert_dedup (tenant_id, symbol, alert_type)
                    VALUES (:tid, :sym, :at)
                """),
                {"tid": self.tenant_id, "sym": symbol, "at": alert_type},
            )

    def _post_to_discord(self, embed: dict) -> None:
        """POST an embed to the tenant's Discord webhook."""
        if not self.discord_webhook_url:
            print(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                f"No Discord webhook URL for tenant {self.tenant_id}. Skipping."
            )
            return
        try:
            result = requests.post(self.discord_webhook_url, json=embed, timeout=10)
            if 200 <= result.status_code < 300:
                print(f"{datetime.now():%Y-%m-%d %H:%M:%S} Sent: {embed['embeds'][0]['title']}")
            else:
                print(
                    f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                    f"Discord error {result.status_code}: {result.text}"
                )
        except requests.RequestException as exc:
            print(f"{datetime.now():%Y-%m-%d %H:%M:%S} Discord request failed: {exc}")

    # ------------------------------------------------------------------
    # Override: PostgreSQL-backed dedup replaces notification.log
    # ------------------------------------------------------------------

    def send_notifications(
        self,
        embed: dict,
        symbol: str = "",
        alert_type: str = "",
    ) -> None:
        """Send a Discord embed with optional time-windowed deduplication.

        If symbol and alert_type are provided, the alert is suppressed when
        the same (tenant_id, symbol, alert_type) fired within its configured
        window. Pass neither to bypass dedup (e.g. heartbeats).
        """
        if symbol and alert_type:
            if self._is_suppressed(symbol, alert_type):
                print(
                    f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                    f"Suppressed duplicate: {alert_type} for {symbol}"
                )
                return
            self._record_fired(symbol, alert_type)

        self._post_to_discord(embed)

    # ------------------------------------------------------------------
    # Agent-specific alert methods
    # ------------------------------------------------------------------

    def send_signal_alert(
        self,
        symbol: str,
        score: int,
        direction: str,
        triggers: list[str],
    ) -> None:
        """Signal Scanner — strong buy or sell signal."""
        is_buy = direction == "buy"
        alert_type = f"signal_{direction}"
        color = 0x00E5FF if is_buy else 0xFF2D78
        emoji = "🟢" if is_buy else "🔴"
        label = "Buy" if is_buy else "Sell"
        trigger_lines = "\n".join(f"• {t}" for t in triggers)

        embed = {
            "content": f"Signal Alert: {datetime.now():%Y-%m-%d %H:%M:%S} {symbol}",
            "embeds": [{
                "title": f"{emoji} {symbol} — Strong {label} Signal  (score {score:+d} / 9)",
                "description": (
                    f"**Triggers:**\n{trigger_lines}\n\n"
                    f"Escalating to Deep Analysis...\n\n"
                    f"[{symbol} chart](https://finance.yahoo.com/chart/{symbol})"
                ),
                "color": color,
            }],
        }
        self.send_notifications(embed, symbol=symbol, alert_type=alert_type)

    def send_recommendation(
        self,
        symbol: str,
        recommendation: str,
        conviction: str,
        entry_low: float | None = None,
        entry_high: float | None = None,
        price_target: float | None = None,
        stop_loss: float | None = None,
        details: dict | None = None,
    ) -> None:
        """Deep Analysis — BUY / SELL / HOLD / AVOID recommendation."""
        alert_type = f"recommendation_{recommendation.lower()}"
        colors = {"BUY": 0x00E676, "SELL": 0xFF3366, "HOLD": 0x00BFFF, "AVOID": 0xFF9100}
        emojis = {"BUY": "✅", "SELL": "🚨", "HOLD": "⏸️", "AVOID": "🚫"}
        color = colors.get(recommendation, 0xFFFFFF)
        emoji = emojis.get(recommendation, "")

        lines = []
        if entry_low and entry_high:
            lines.append(f"**Entry:** ${entry_low:.2f} – ${entry_high:.2f}")
        if price_target:
            lines.append(f"**Target:** ${price_target:.2f}")
        if stop_loss:
            lines.append(f"**Stop:** ${stop_loss:.2f}")

        if details:
            if bull := details.get("bull_case"):
                lines.append("\n**Bull case:**\n" + "\n".join(f"• {b}" for b in bull))
            if bear := details.get("bear_case"):
                lines.append("\n**Bear case:**\n" + "\n".join(f"• {b}" for b in bear))
            if options_play := details.get("options_play"):
                lines.append(f"\n**Options play:** {options_play}")

        lines.append(f"\n[{symbol} chart](https://finance.yahoo.com/chart/{symbol})")

        embed = {
            "content": f"Deep Analysis: {datetime.now():%Y-%m-%d %H:%M:%S} {symbol}",
            "embeds": [{
                "title": f"{emoji} {symbol} — {recommendation}  |  {conviction} Conviction",
                "description": "\n".join(lines),
                "color": color,
            }],
        }
        self.send_notifications(embed, symbol=symbol, alert_type=alert_type)

    def send_portfolio_alert(
        self,
        alert_type: str,
        symbol: str,
        details: dict,
    ) -> None:
        """Portfolio Monitor — AT RISK or INSTITUTIONAL EXIT per-position alert."""
        _colors = {
            "portfolio_at_risk":   0xFF3366,
            "portfolio_inst_exit": 0xFF9100,
        }
        _titles = {
            "portfolio_at_risk":   f"⚠️ {symbol} — Position AT RISK",
            "portfolio_inst_exit": f"🏦 {symbol} — Institutional Exit Signal",
        }
        color = _colors.get(alert_type, 0xFFFFFF)
        title = _titles.get(alert_type, f"{symbol} — Portfolio Alert")

        desc_lines = [f"{k}: {v}" for k, v in details.items() if k != "escalate"]
        if details.get("escalate"):
            desc_lines.append("\nEscalating to Deep Analysis.")
        desc_lines.append(f"\n[{symbol} chart](https://finance.yahoo.com/chart/{symbol})")

        embed = {
            "content": f"Portfolio Alert: {datetime.now():%Y-%m-%d %H:%M:%S} {symbol}",
            "embeds": [{
                "title": title,
                "description": "\n".join(desc_lines),
                "color": color,
            }],
        }
        self.send_notifications(embed, symbol=symbol, alert_type=alert_type)

    def send_morning_report(self, report: dict) -> None:
        """Portfolio Monitor — consolidated morning or closing report."""
        lines = []

        if at_risk := report.get("at_risk"):
            lines.append("**⚠️ AT RISK**")
            for item in at_risk:
                lines.append(
                    f"{item['symbol']} ${item['price']:.2f} — "
                    f"stop ${item['stop']:.2f} — gap {item['gap_pct']:.1f}%"
                )

        if degrading := report.get("trend_degrading"):
            lines.append("\n**📉 TREND DEGRADING**")
            for item in degrading:
                lines.append(f"{item['symbol']} — {item['detail']}")

        if squeeze := report.get("squeeze_watch"):
            lines.append("\n**🔥 SQUEEZE WATCH**")
            for item in squeeze:
                lines.append(f"{item['symbol']} — {item['detail']}")

        if caps := report.get("capitulation"):
            lines.append("\n**🔄 CAPITULATION SIGNAL**")
            for item in caps:
                lines.append(f"{item['symbol']} — {item['detail']}")

        if opps := report.get("watchlist_opportunities"):
            lines.append("\n**📋 WATCHLIST OPPORTUNITIES**")
            for i, opp in enumerate(opps[:5], 1):
                lines.append(f"#{i} {opp['symbol']} score {opp['score']} — {opp['detail']}")

        if escalated := report.get("escalated"):
            lines.append(f"\n**🎯 Escalated for Deep Analysis:** {', '.join(escalated)}")

        report_type = report.get("type", "Morning")
        embed = {
            "content": f"Portfolio Health Report: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [{
                "title": (
                    f"📊 Portfolio Health — {report_type} Report  "
                    f"{datetime.now():%Y-%m-%d}"
                ),
                "description": "\n".join(lines) if lines else "No alerts to report.",
                "color": 0xE040FB,
            }],
        }
        # Use a synthetic symbol key — reports are portfolio-wide, not symbol-specific
        self.send_notifications(embed, symbol="__portfolio__", alert_type="portfolio_report")

    def send_heartbeat(self, status: str = "ok", message: str = "") -> None:
        """Orchestrator — periodic liveness ping. Always fires; no dedup."""
        emoji = "✅" if status == "ok" else "❌"
        embed = {
            "content": f"Orchestrator: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [{
                "title": f"{emoji} Orchestrator Heartbeat — {status.upper()}",
                "description": message or "All systems nominal.",
                "color": 0x808080,
            }],
        }
        self._post_to_discord(embed)
