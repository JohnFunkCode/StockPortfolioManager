"""`StopsService` and the /api/portfolio/stops routes (#431 plan step 4.1).

Runs the service against the real repositories and the test database, with a
stub in place of `PricesService` (a stop is set against the last stored
close, and the suite stores no bars), and the routes through TestClient with
two owners reached through `owner_identities`.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal
from unittest.mock import Mock, patch

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.repositories.stops_repository import StopsRepository  # noqa: E402
from quantcore.services.stops import (  # noqa: E402
    MAX_SOURCE_REF_LENGTH,
    MAX_STOP_LEVEL,
    StopNotFoundError,
    StopsService,
)

from api.auth import Principal, require_principal  # noqa: E402
from api.main import create_app  # noqa: E402

from tests.test_stops_repository import lot, purge  # noqa: E402

OWNER = "zz_stops_api_owner"
OTHER = "zz_stops_api_other"
IDENTITY = "zz_stops_api_owner@example.com"
OTHER_IDENTITY = "zz_stops_api_other@example.com"
SYMBOL = "ZZSTOPAPI"
UNHELD = "ZZSTOPAPINONE"
D = Decimal

# Monday 2026-06-15, 15:00 UTC. The last closed bar is Friday the 12th.
NOW = datetime.datetime(2026, 6, 15, 15, 0, tzinfo=datetime.timezone.utc)
LATER = NOW + datetime.timedelta(hours=1)
BAR = datetime.datetime(2026, 6, 12, tzinfo=datetime.timezone.utc)


class StubPrices:
    """`last_close` from a dict, counting the calls. A float close, as the
    real service returns."""

    def __init__(self):
        self.closes = {SYMBOL: 100.0}
        self.calls = 0

    def last_close(self, symbol):
        self.calls += 1
        close = self.closes.get(symbol)
        return {"close": close, "as_of": BAR if close is not None else None}


def _purge():
    purge((OWNER, OTHER), (SYMBOL, UNHELD))
    with closing(get_connection()) as conn:
        for identity in (IDENTITY, OTHER_IDENTITY):
            conn.execute("DELETE FROM owner_identities WHERE identity = %s", (identity,))
        conn.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.books = BooksRepository()
        self.lots = PortfolioRepository()
        self.repo = StopsRepository(self.lots)
        self.prices = StubPrices()
        self.service = StopsService(self.repo, self.books, self.prices, Mock())
        # A real holding and a paper holding of the same symbol.
        self.lots.add_position(OWNER, lot(SYMBOL))
        self.paper = self.books.create_paper_book(owner=OWNER, name="Paper", starting_cash=0)
        self.lots.add_position(OWNER, lot(SYMBOL), book_id=self.paper)

    def set_stop(self, **overrides):
        args = {"owner": OWNER, "symbol": SYMBOL, "kind": "fixed", "stop_level": "90", "now": NOW}
        args.update(overrides)
        return self.service.set_stop(**args)

    def stops(self, **kwargs):
        return self.service.list_stops(owner=OWNER, **kwargs)

    def row_count(self, owner=OWNER):
        with closing(get_connection()) as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM stops WHERE owner = %s", (owner,)
            ).fetchone()["n"]


class SetStopTest(_Base):
    def test_a_fixed_stop_records_what_it_was_set_against(self):
        stop = self.set_stop(stop_level="91.456", method="confluence", proposed_level="90.111")

        self.assertEqual(
            {k: stop[k] for k in (
                "symbol", "kind", "stop_level", "trail_pct", "high_water_mark", "method",
                "proposed_level", "reference_price", "as_of", "source", "source_ref", "status",
                "created_at", "replaced_stop_id",
            )},
            {
                "symbol": SYMBOL, "kind": "fixed", "stop_level": D("91.46"), "trail_pct": None,
                "high_water_mark": None, "method": "confluence", "proposed_level": D("90.11"),
                "reference_price": D("100.00"), "as_of": "2026-06-12", "source": "manual",
                "source_ref": None, "status": "active", "created_at": NOW,
                "replaced_stop_id": None,
            },
        )
        self.assertEqual(self.prices.calls, 1)

    def test_a_trailing_stop_starts_from_the_last_close(self):
        self.prices.closes[SYMBOL] = 33.329999999
        stop = self.set_stop(kind="trailing", stop_level=None, trail_pct="7.5", method="trailing")

        self.assertEqual(
            (stop["kind"], stop["high_water_mark"], stop["trail_pct"], stop["stop_level"]),
            ("trailing", D("33.33"), D("7.5"), D("30.83")),
        )
        self.assertEqual(stop["reference_price"], D("33.33"))

    def test_setting_another_stop_replaces_the_first(self):
        first = self.set_stop()
        second = self.set_stop(stop_level="95", now=LATER)

        self.assertEqual(second["replaced_stop_id"], first["stop_id"])
        listed = self.stops()
        self.assertEqual((listed["book_kind"], listed["active_count"]), ("real", 1))
        self.assertEqual([s["stop_id"] for s in listed["stops"]], [second["stop_id"]])
        everything = self.stops(include_inactive=True)
        self.assertEqual(
            [(s["stop_id"], s["status"]) for s in everything["stops"]],
            [(second["stop_id"], "active"), (first["stop_id"], "replaced")],
        )
        self.assertEqual(everything["active_count"], 1)

    def test_a_stop_at_or_above_the_last_close_is_refused(self):
        for level in ("100", "100.004", "250"):
            with self.subTest(level=level), self.assertRaisesRegex(ValueError, "below the last close"):
                self.set_stop(stop_level=level)
        self.assertEqual(self.row_count(), 0)

    def test_with_no_stored_close_a_fixed_stop_is_dated_today(self):
        self.prices.closes[SYMBOL] = None
        # 01:00 UTC on the 16th is still the 15th in New York.
        late = datetime.datetime(2026, 6, 16, 1, 0, tzinfo=datetime.timezone.utc)
        stop = self.set_stop(stop_level="500", now=late)

        self.assertEqual(
            (stop["stop_level"], stop["reference_price"], stop["as_of"]),
            (D("500.00"), None, "2026-06-15"),
        )
        with self.assertRaisesRegex(ValueError, "none is on record"):
            self.set_stop(kind="trailing", stop_level=None, trail_pct="8")

    def test_a_symbol_the_book_does_not_hold_is_refused(self):
        self.prices.closes[UNHELD] = 100.0
        with self.assertRaisesRegex(ValueError, "no open lot of " + UNHELD):
            self.set_stop(symbol=UNHELD)
        self.assertEqual(self.row_count(), 0)

    def test_an_owner_with_no_book_is_refused_and_gets_none(self):
        with self.assertRaisesRegex(ValueError, "no open lot"):
            self.set_stop(owner=OTHER)
        self.assertEqual(self.books.list_books(owner=OTHER), [])

    def test_bad_input_is_refused_and_writes_nothing(self):
        cases = [
            ({"symbol": "not a ticker"}, "valid ticker"),
            ({"kind": "limit"}, "fixed or trailing"),
            ({"stop_level": "abc"}, "not a valid number"),
            ({"stop_level": "NaN"}, "finite"),
            ({"stop_level": "Infinity"}, "finite"),
            ({"stop_level": "-5"}, "more than zero"),
            ({"stop_level": str(MAX_STOP_LEVEL + 1)}, "too large"),
            ({"stop_level": None}, "needs a stop_level"),
            ({"trail_pct": "5"}, "not a trail_pct"),
            ({"kind": "trailing", "stop_level": None}, "needs a trail_pct"),
            ({"kind": "trailing", "stop_level": None, "trail_pct": "100"}, "less than 100"),
            ({"kind": "trailing", "stop_level": None, "trail_pct": "NaN"}, "finite"),
            ({"method": "gut feeling"}, "method must be one of"),
            ({"source": "stop"}, "source must be one of"),
            ({"proposed_level": "0"}, "more than zero"),
            ({"proposed_level": "NaN"}, "finite"),
            ({"source_ref": "x" * (MAX_SOURCE_REF_LENGTH + 1)}, "source_ref"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                self.set_stop(**overrides)
        self.assertEqual(self.row_count(), 0)

    def test_a_stop_in_a_paper_book_is_that_books_own(self):
        real = self.set_stop()
        paper = self.set_stop(stop_level="85", book_id=self.paper)

        self.assertIsNone(paper["replaced_stop_id"])
        self.assertEqual(paper["book_id"], self.paper)
        self.assertEqual([s["stop_id"] for s in self.stops()["stops"]], [real["stop_id"]])
        listed = self.stops(book_id=self.paper)
        self.assertEqual(listed["book_kind"], "paper")
        self.assertEqual([s["stop_id"] for s in listed["stops"]], [paper["stop_id"]])

    def test_an_archived_book_takes_no_new_stop_but_gives_one_up(self):
        stop = self.set_stop(book_id=self.paper)
        self.books.archive_book(owner=OWNER, book_id=self.paper)

        with self.assertRaisesRegex(ValueError, "archived"):
            self.set_stop(stop_level="80", book_id=self.paper, now=LATER)
        cancelled = self.service.cancel_stop(
            owner=OWNER, stop_id=stop["stop_id"], book_id=self.paper, now=LATER
        )
        self.assertEqual(cancelled["status"], "cancelled")

    def test_a_book_that_is_not_the_owners_is_not_found(self):
        with self.assertRaises(BookNotFoundError):
            self.set_stop(owner=OTHER, book_id=self.paper)
        with self.assertRaises(BookNotFoundError):
            self.service.list_stops(owner=OTHER, book_id=self.paper)
        with self.assertRaises(BookNotFoundError):
            self.service.cancel_stop(owner=OTHER, stop_id=1, book_id=self.paper)


class BrokerMirrorTest(_Base):
    """A stop that mirrors an order placed at an outside broker (plan step 4.5)."""

    def test_a_mirrored_stop_records_the_brokers_order(self):
        stop = self.set_stop(
            stop_level="88", at_broker=True, broker_quantity="4", broker_order_type="stop_limit",
            brokerage="  Schwab  ",
        )
        self.assertEqual(
            {k: stop[k] for k in (
                "stop_level", "method", "at_broker", "broker_quantity", "broker_order_type",
                "brokerage", "status")},
            {"stop_level": D("88.00"), "method": "manual", "at_broker": True,
             "broker_quantity": D("4"), "broker_order_type": "stop_limit",
             "brokerage": "Schwab", "status": "active"},
        )
        self.assertEqual(self.service.list_stops(owner=OWNER)["stops"][0]["brokerage"], "Schwab")

    def test_every_detail_is_optional(self):
        stop = self.set_stop(at_broker=True)
        self.assertEqual(
            (stop["at_broker"], stop["broker_quantity"], stop["broker_order_type"],
             stop["brokerage"]),
            (True, None, None, None),
        )

    def test_a_stop_only_here_carries_no_broker_details(self):
        stop = self.set_stop()
        self.assertEqual(
            (stop["at_broker"], stop["broker_quantity"], stop["broker_order_type"],
             stop["brokerage"]),
            (False, None, None, None),
        )

    def test_a_trailing_mirror_is_a_trailing_stop_order(self):
        stop = self.set_stop(
            kind="trailing", stop_level=None, trail_pct="8", at_broker=True,
            broker_order_type="trailing_stop",
        )
        self.assertEqual((stop["kind"], stop["broker_order_type"]), ("trailing", "trailing_stop"))

    def test_a_replacement_does_not_inherit_the_mirror(self):
        mirrored = self.set_stop(at_broker=True, brokerage="Schwab")
        here = self.set_stop(stop_level="95", now=LATER)
        self.assertEqual(here["replaced_stop_id"], mirrored["stop_id"])
        self.assertEqual((here["at_broker"], here["brokerage"]), (False, None))

    def test_a_paper_book_has_no_broker(self):
        with self.assertRaisesRegex(ValueError, "only a real book's stop can be at a broker"):
            self.set_stop(book_id=self.paper, at_broker=True)
        self.assertEqual(self.row_count(), 0)

    def test_the_real_book_named_by_its_id_can_mirror(self):
        real = next(b for b in self.books.list_books(owner=OWNER) if b["kind"] == "real")
        stop = self.set_stop(book_id=real["book_id"], at_broker=True)
        self.assertTrue(stop["at_broker"])

    def test_more_shares_than_the_book_holds_is_refused(self):
        with self.assertRaisesRegex(ValueError, "for 6 shares, and this book holds 5"):
            self.set_stop(at_broker=True, broker_quantity="6")
        self.assertEqual(self.row_count(), 0)
        self.assertEqual(self.set_stop(at_broker=True, broker_quantity="5")["broker_quantity"], D("5"))

    def test_bad_broker_details_are_refused_and_write_nothing(self):
        cases = [
            ({"brokerage": "Schwab"}, "set at_broker"),
            ({"broker_quantity": "2"}, "set at_broker"),
            ({"broker_order_type": "stop"}, "set at_broker"),
            ({"at_broker": True, "broker_quantity": "0"}, "more than zero"),
            ({"at_broker": True, "broker_quantity": "-1"}, "more than zero"),
            ({"at_broker": True, "broker_quantity": "NaN"}, "finite"),
            ({"at_broker": True, "broker_quantity": "1.0000001"}, "six decimal places"),
            ({"at_broker": True, "broker_order_type": "trailing_stop"}, "must be one of stop, stop_limit"),
            ({"at_broker": True, "broker_order_type": "market"}, "must be one of"),
            ({"at_broker": True, "brokerage": "x" * 61}, "at most 60"),
            ({"kind": "trailing", "stop_level": None, "trail_pct": "8", "at_broker": True,
              "broker_order_type": "stop"}, "must be one of trailing_stop"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                self.set_stop(**overrides)
        self.assertEqual(self.row_count(), 0)


class CancelStopTest(_Base):
    def test_cancel_takes_an_active_stop_off_once(self):
        stop = self.set_stop()
        cancelled = self.service.cancel_stop(owner=OWNER, stop_id=stop["stop_id"], now=LATER)

        self.assertEqual((cancelled["status"], cancelled["resolved_at"]), ("cancelled", LATER))
        self.assertEqual(self.stops()["stops"], [])
        with self.assertRaisesRegex(ValueError, "this one is cancelled"):
            self.service.cancel_stop(owner=OWNER, stop_id=stop["stop_id"], now=LATER)

    def test_a_refused_cancel_names_the_status_the_stop_ended_in(self):
        # The stop is resolved by something else between the read and the
        # conditional UPDATE: the message names what it is now, not "active".
        stop = self.set_stop()
        resolve = self.service._repo.resolve_stop

        def raced(**kwargs):
            resolve(**{**kwargs, "status": "closed"})
            return resolve(**kwargs)

        with patch.object(self.service._repo, "resolve_stop", side_effect=raced):
            with self.assertRaisesRegex(ValueError, "this one is closed"):
                self.service.cancel_stop(owner=OWNER, stop_id=stop["stop_id"], now=LATER)

    def test_cancel_cannot_reach_another_book_or_owner(self):
        stop = self.set_stop(book_id=self.paper)
        for owner, book in ((OWNER, None), (OTHER, None)):
            with self.subTest(owner=owner), self.assertRaises(StopNotFoundError):
                self.service.cancel_stop(owner=owner, stop_id=stop["stop_id"], book_id=book)
        self.assertEqual(self.stops(book_id=self.paper)["active_count"], 1)

    def test_a_read_creates_no_book(self):
        self.assertEqual(
            self.service.list_stops(owner=OTHER),
            {"book_kind": "real", "active_count": 0, "stops": []},
        )
        self.assertEqual(self.books.list_books(owner=OTHER), [])


class HoldingStopsTest(_Base):
    """`StopsService.holding_stops` (plan decision 66) over the real tables."""

    def shown(self, **kwargs):
        return self.service.holding_stops(owner=OWNER, **kwargs)

    def test_each_book_shows_its_own_holdings_stop(self):
        real = self.set_stop()
        paper = self.set_stop(book_id=self.paper, stop_level="85")

        self.assertEqual(self.shown()["book_kind"], "real")
        self.assertEqual([s["stop_id"] for s in self.shown()["stops"]], [real["stop_id"]])
        answer = self.shown(book_id=self.paper)
        self.assertEqual(answer["book_kind"], "paper")
        self.assertEqual([(s["stop_id"], s["stop_level"]) for s in answer["stops"]],
                         [(paper["stop_id"], D("85"))])

    def test_a_replaced_stop_gives_way_and_a_cancelled_one_shows_nothing(self):
        self.set_stop()
        newer = self.set_stop(stop_level="88")
        self.assertEqual([s["stop_id"] for s in self.shown()["stops"]], [newer["stop_id"]])

        self.service.cancel_stop(owner=OWNER, stop_id=newer["stop_id"], now=NOW)
        self.assertEqual(self.shown()["stops"], [])

    def test_a_reached_stop_is_shown_while_the_holding_it_was_reached_on_is_open(self):
        stop = self.set_stop()
        reached_on = datetime.date(2026, 6, 16)
        self.assertTrue(self.repo.trigger_stop(
            owner=OWNER, stop_id=stop["stop_id"], stop_level=D("90"), high_water_mark=None,
            triggered_session=reached_on, trigger_price=D("89"), resolved_at=NOW))

        (shown,) = self.shown()["stops"]
        self.assertEqual((shown["status"], shown["trigger_price"]), ("triggered", D("89")))

    def test_a_reached_stop_is_not_shown_on_a_position_bought_after_it(self):
        stop = self.set_stop()
        self.assertTrue(self.repo.trigger_stop(
            owner=OWNER, stop_id=stop["stop_id"], stop_level=D("90"), high_water_mark=None,
            triggered_session=datetime.date(2025, 12, 1), trigger_price=D("89"),
            resolved_at=NOW))
        # The lot in `setUp` was opened on 2026-01-02, after that bar.
        self.assertEqual(self.shown()["stops"], [])

    def test_the_read_creates_no_book_and_makes_no_network_call(self):
        self.assertEqual(self.service.holding_stops(owner=OTHER), {"book_kind": "real", "stops": []})
        self.assertEqual(self.books.list_books(owner=OTHER), [])
        self.assertEqual(self.prices.calls, 0)

    def test_another_owners_book_is_not_found(self):
        self.set_stop(book_id=self.paper)
        with self.assertRaises(BookNotFoundError):
            self.service.holding_stops(owner=OTHER, book_id=self.paper)


class StopsRoutesTest(_Base):
    def setUp(self):
        super().setUp()
        identities = OwnerIdentityRepository()
        identities.upsert(identity=IDENTITY, owner=OWNER)
        identities.upsert(identity=OTHER_IDENTITY, owner=OTHER)
        self.app = create_app()
        self.client = TestClient(self.app)
        self.as_identity(IDENTITY)
        services = patch("api.routers.stops.services")
        services.start().return_value.stops = self.service
        self.addCleanup(services.stop)

    def as_identity(self, identity):
        principal = Principal(subject=identity, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal

    def post(self, **body):
        body = {"symbol": SYMBOL, "kind": "fixed", "stop_level": "90", **body}
        return self.client.post("/api/portfolio/stops", json=body)

    def test_set_list_and_cancel(self):
        made = self.post(method="technical", proposed_level="89.5")
        self.assertEqual(made.status_code, 201)
        stop = made.json()
        self.assertEqual(
            (stop["symbol"], stop["kind"], stop["stop_level"], stop["method"], stop["status"],
             stop["proposed_level"], stop["reference_price"], stop["as_of"]),
            (SYMBOL, "fixed", 90, "technical", "active", 89.5, 100, "2026-06-12"),
        )
        self.assertNotIn("owner", stop)

        listed = self.client.get("/api/portfolio/stops").json()
        self.assertEqual((listed["book_kind"], listed["active_count"]), ("real", 1))
        self.assertEqual([s["stop_id"] for s in listed["stops"]], [stop["stop_id"]])

        cancelled = self.client.post(f"/api/portfolio/stops/{stop['stop_id']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(self.client.get("/api/portfolio/stops").json()["stops"], [])
        everything = self.client.get("/api/portfolio/stops", params={"include_inactive": "true"})
        self.assertEqual([s["status"] for s in everything.json()["stops"]], ["cancelled"])
        again = self.client.post(f"/api/portfolio/stops/{stop['stop_id']}/cancel")
        self.assertEqual(again.status_code, 422)

    def test_a_trailing_stop_and_a_replacement(self):
        first = self.post().json()
        trailing = self.post(kind="trailing", stop_level=None, trail_pct="8")
        self.assertEqual(trailing.status_code, 201)
        body = trailing.json()
        self.assertEqual(
            (body["stop_level"], body["trail_pct"], body["high_water_mark"], body["replaced_stop_id"]),
            (92, 8, 100, first["stop_id"]),
        )

    def test_a_paper_book_is_named_in_the_request(self):
        made = self.post(book_id=self.paper)
        self.assertEqual(made.status_code, 201)
        stop = made.json()
        self.assertEqual(stop["book_id"], self.paper)
        self.assertEqual(self.client.get("/api/portfolio/stops").json()["stops"], [])
        listed = self.client.get("/api/portfolio/stops", params={"book_id": self.paper}).json()
        self.assertEqual((listed["book_kind"], listed["active_count"]), ("paper", 1))
        # Without its book the stop is not found.
        self.assertEqual(
            self.client.post(f"/api/portfolio/stops/{stop['stop_id']}/cancel").status_code, 404
        )
        cancel = self.client.post(
            f"/api/portfolio/stops/{stop['stop_id']}/cancel", params={"book_id": self.paper}
        )
        self.assertEqual(cancel.status_code, 200)

    def test_refusals_are_422_and_say_why(self):
        cases = [
            ({"stop_level": "100"}, "below the last close"),
            ({"symbol": UNHELD}, "no open lot"),
            ({"kind": "trailing", "stop_level": None}, "needs a trail_pct"),
            ({"trail_pct": "5"}, "not a trail_pct"),
        ]
        for body, message in cases:
            with self.subTest(body=body):
                response = self.post(**body)
                self.assertEqual(response.status_code, 422)
                self.assertIn(message, str(response.json()))
        # The request model refuses these before the service sees them.
        for body in ({"kind": "limit"}, {"method": "gut feeling"}, {"stop_level": "abc"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(**body).status_code, 422)
        self.assertEqual(self.row_count(), 0)

    def test_the_route_takes_no_source_status_or_owner(self):
        made = self.post(source="ai", source_ref="forged", status="triggered", owner=OTHER)
        self.assertEqual(made.status_code, 201)
        body = made.json()
        self.assertEqual((body["source"], body["source_ref"], body["status"]), ("manual", None, "active"))
        self.assertEqual(self.row_count(OTHER), 0)

    def test_holdings_answers_one_stop_a_holding_for_the_asked_book(self):
        real = self.post().json()
        self.post(stop_level="88")              # replaces it: only the newest shows
        paper = self.post(book_id=self.paper).json()

        answer = self.client.get("/api/portfolio/stops/holdings")
        self.assertEqual(answer.status_code, 200)
        body = answer.json()
        self.assertEqual(body["book_kind"], "real")
        self.assertEqual([(s["symbol"], s["stop_level"], s["status"]) for s in body["stops"]],
                         [(SYMBOL, 88, "active")])
        self.assertNotEqual(body["stops"][0]["stop_id"], real["stop_id"])

        in_paper = self.client.get(
            "/api/portfolio/stops/holdings", params={"book_id": self.paper}).json()
        self.assertEqual([s["stop_id"] for s in in_paper["stops"]], [paper["stop_id"]])

    def test_holdings_is_closed_to_another_owner(self):
        self.post()
        self.post(book_id=self.paper)
        self.as_identity(OTHER_IDENTITY)
        self.assertEqual(self.client.get("/api/portfolio/stops/holdings").json()["stops"], [])
        self.assertEqual(
            self.client.get(
                "/api/portfolio/stops/holdings", params={"book_id": self.paper}).status_code,
            404)

    def test_another_owner_reaches_nothing_by_any_route(self):
        real = self.post().json()
        paper = self.post(book_id=self.paper).json()
        self.as_identity(OTHER_IDENTITY)

        # Their own real book is empty and holds nothing to stop ...
        self.assertEqual(self.client.get("/api/portfolio/stops").json()["stops"], [])
        self.assertEqual(self.post().status_code, 422)
        # ... this owner's paper book does not exist for them ...
        self.assertEqual(
            self.client.get("/api/portfolio/stops", params={"book_id": self.paper}).status_code, 404
        )
        self.assertEqual(self.post(book_id=self.paper).status_code, 404)
        # ... and neither stop can be cancelled, with or without the book.
        for stop, params in ((real, {}), (paper, {}), (paper, {"book_id": self.paper})):
            cancel = self.client.post(
                f"/api/portfolio/stops/{stop['stop_id']}/cancel", params=params
            )
            self.assertEqual(cancel.status_code, 404)

        self.assertEqual(self.row_count(OTHER), 0)
        self.assertEqual(self.stops()["active_count"], 1)
        self.assertEqual(self.stops(book_id=self.paper)["active_count"], 1)

    def test_a_broker_mirror_over_the_route(self):
        made = self.post(at_broker=True, broker_quantity="3", broker_order_type="stop",
                         brokerage="Fidelity")
        self.assertEqual(made.status_code, 201)
        body = made.json()
        self.assertEqual(
            (body["at_broker"], body["broker_quantity"], body["broker_order_type"],
             body["brokerage"]),
            (True, 3, "stop", "Fidelity"),
        )
        holding = self.client.get("/api/portfolio/stops/holdings").json()["stops"][0]
        self.assertEqual((holding["at_broker"], holding["brokerage"]), (True, "Fidelity"))
        # A paper book has no broker, and an unknown order type is refused
        # by the request model.
        self.assertEqual(self.post(book_id=self.paper, at_broker=True).status_code, 422)
        self.assertEqual(
            self.post(at_broker=True, broker_order_type="market").status_code, 422)
        # Neither refusal wrote a stop or took the mirrored one's place.
        self.assertEqual(self.stops(book_id=self.paper)["stops"], [])
        self.assertEqual([s["stop_id"] for s in self.stops()["stops"]], [body["stop_id"]])


if __name__ == "__main__":
    unittest.main()
