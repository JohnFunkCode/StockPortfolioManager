import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from notifier import Notifier
from portfolio.metrics import Metrics
from portfolio.portfolio import Portfolio
from portfolio.stock import Stock


def _lot(symbol, name, purchase_price, quantity, purchase_date, current_price):
    stock = Stock(
        name=name,
        symbol=symbol,
        quantity=quantity,
        purchase_price=purchase_price,
        purchase_date=purchase_date,
        current_price=current_price,
    )
    # Non-None and below current_price so the moving-average checks in
    # calculate_and_send_notifications() don't fire (or crash comparing
    # against the Metrics() default of None).
    stock.metrics = Metrics(
        thirty_day_moving_average=1,
        fifty_day_moving_average=1,
        one_hundred_day_moving_average=1,
        two_hundred_day_moving_average=1,
    )
    return stock


class NotifierMultiLotLossAlertTest(unittest.TestCase):
    """Two-lot fixture for issue #126 Step 3.4 — the loss alert must be
    evaluated per lot, but still consolidated into one Discord notification
    per symbol (dedup key is symbol + alert type, not lot)."""

    def _make_notifier(self, portfolio: Portfolio) -> tuple[Notifier, list]:
        harvester_stub = MagicMock()
        harvester_stub.harvest_hit_for_symbol.return_value = []
        services_stub = MagicMock()
        services_stub.harvester = harvester_stub
        patcher = patch("notifier.get_services", return_value=services_stub)
        patcher.start()
        self.addCleanup(patcher.stop)

        notifier = Notifier(portfolio)
        notifier.check_options_alerts = MagicMock()
        notifier.check_sentiment_flips = MagicMock()

        sent_embeds: list = []
        notifier.send_notifications = MagicMock(side_effect=sent_embeds.append)
        return notifier, sent_embeds

    def test_one_underwater_lot_and_one_profitable_lot_yields_one_alert(self):
        portfolio = Portfolio()
        losing_lot = _lot(
            "ZZLOSSTEST", "ZZ Loss Test", purchase_price=50.0, quantity=10,
            purchase_date=date(2026, 1, 2), current_price=40.0,
        )
        winning_lot = _lot(
            "ZZLOSSTEST", "ZZ Loss Test", purchase_price=30.0, quantity=5,
            purchase_date=date(2026, 2, 3), current_price=40.0,
        )
        portfolio.add_stock(losing_lot)
        portfolio.add_stock(winning_lot)

        notifier, sent_embeds = self._make_notifier(portfolio)
        notifier.calculate_and_send_notifications()

        loss_alerts = [
            e for e in sent_embeds
            if e["embeds"][0]["title"] == "ZZ Loss Test (ZZLOSSTEST) Loss Alert"
        ]
        self.assertEqual(
            len(loss_alerts), 1,
            "expected exactly one consolidated Loss Alert, not one per lot",
        )

        description = loss_alerts[0]["embeds"][0]["description"]
        self.assertIn("2026-01-02", description)
        self.assertNotIn("2026-02-03", description)

    def test_no_lots_underwater_yields_no_loss_alert(self):
        portfolio = Portfolio()
        lot_a = _lot(
            "ZZGAINTEST", "ZZ Gain Test", purchase_price=10.0, quantity=10,
            purchase_date=date(2026, 1, 2), current_price=40.0,
        )
        lot_b = _lot(
            "ZZGAINTEST", "ZZ Gain Test", purchase_price=20.0, quantity=5,
            purchase_date=date(2026, 2, 3), current_price=40.0,
        )
        portfolio.add_stock(lot_a)
        portfolio.add_stock(lot_b)

        notifier, sent_embeds = self._make_notifier(portfolio)
        notifier.calculate_and_send_notifications()

        loss_alerts = [
            e for e in sent_embeds
            if e["embeds"][0]["title"] == "ZZ Gain Test (ZZGAINTEST) Loss Alert"
        ]
        self.assertEqual(loss_alerts, [])

    def test_both_lots_underwater_yields_one_alert_listing_both(self):
        portfolio = Portfolio()
        lot_a = _lot(
            "ZZBOTHLOSS", "ZZ Both Loss", purchase_price=50.0, quantity=10,
            purchase_date=date(2026, 1, 2), current_price=40.0,
        )
        lot_b = _lot(
            "ZZBOTHLOSS", "ZZ Both Loss", purchase_price=60.0, quantity=5,
            purchase_date=date(2026, 2, 3), current_price=40.0,
        )
        portfolio.add_stock(lot_a)
        portfolio.add_stock(lot_b)

        notifier, sent_embeds = self._make_notifier(portfolio)
        notifier.calculate_and_send_notifications()

        loss_alerts = [
            e for e in sent_embeds
            if e["embeds"][0]["title"] == "ZZ Both Loss (ZZBOTHLOSS) Loss Alert"
        ]
        self.assertEqual(len(loss_alerts), 1)

        description = loss_alerts[0]["embeds"][0]["description"]
        self.assertIn("2026-01-02", description)
        self.assertIn("2026-02-03", description)


if __name__ == "__main__":
    unittest.main()
