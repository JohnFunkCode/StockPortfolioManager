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


class NotifierSentimentFlipTest(unittest.TestCase):
    """The flip alert now goes through analytics.sentiment.classify_flip (#411);
    which pairs alert, and their colours, must not change."""

    def _alerts(self, prev, new):
        portfolio = Portfolio()
        portfolio.add_stock(_lot("ZZFLIP", "ZZ Flip", 10.0, 1, date(2026, 1, 2), 11.0))
        store = MagicMock()
        store.get_latest.return_value = {"overall_sentiment": new, "captured_at": "2026-10-23T22:00"}
        store.get_prior.return_value = {"overall_sentiment": prev}
        with patch("notifier.get_services", return_value=MagicMock()), \
             patch("quantcore.repositories.sentiment_repository.SentimentStore",
                   return_value=store):
            notifier = Notifier(portfolio)
            sent: list = []
            notifier.send_notifications = MagicMock(side_effect=sent.append)
            notifier.check_sentiment_flips()
        return sent

    def test_which_changes_alert(self):
        cases = [
            ("positive", "negative", 0xFF4444),
            ("neutral", "negative", 0xFF4444),
            ("negative", "positive", 0x00CC66),
            ("negative", "neutral", 0x00CC66),
            ("positive", "neutral", None),
            ("neutral", "positive", None),
            ("negative", "negative", None),
            (None, "negative", None),
        ]
        for prev, new, colour in cases:
            with self.subTest(prev=prev, new=new):
                sent = self._alerts(prev, new)
                if colour is None:
                    self.assertEqual(sent, [])
                else:
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(sent[0]["embeds"][0]["color"], colour)


class NotifierOptionsAlertsOwnerTests(unittest.TestCase):
    """check_options_alerts reads only the run's owner's options positions
    (#460): the webhook is that owner's channel."""

    def test_every_store_call_is_scoped_to_the_notifier_owner(self):
        services_stub = MagicMock()
        services_stub.prices.get_fast_price.return_value = 110.0
        # check_options_alerts re-imports get_services locally, so patch the
        # registry's name as well as the notifier module's.
        for target in ("notifier.get_services",
                       "quantcore.services.registry.get_services"):
            patcher = patch(target, return_value=services_stub)
            patcher.start()
            self.addCleanup(patcher.stop)

        notifier = Notifier(Portfolio(), owner="ownerb")
        store = MagicMock()
        store.auto_expire_past_positions.return_value = []
        store.get_active_positions.return_value = [{"symbol": "ZZOPT"}]
        store.get_pending_alerts.return_value = []
        notifier._options_positions = store
        notifier.send_notifications = MagicMock()

        notifier.check_options_alerts()

        store.auto_expire_past_positions.assert_called_once_with(owner="ownerb")
        store.get_active_positions.assert_called_once_with(owner="ownerb")
        store.get_pending_alerts.assert_called_once_with(
            {"ZZOPT": 110.0}, owner="ownerb"
        )
        notifier.send_notifications.assert_not_called()
