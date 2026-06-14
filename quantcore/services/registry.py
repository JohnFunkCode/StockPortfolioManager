"""Composition root — the only place objects are wired together.

Architectural standard v2 §5.2: adapters (MCP tools, Flask routes, CLI
scripts) call ``get_services()`` and invoke exactly one method on the result.
Service modules never import this registry or each other; all dependency
wiring is constructor injection performed here.

Construction is lazy (``lru_cache``) so importing this module costs nothing —
important for MCP stdio servers, whose clients enforce startup timeouts.

Service fields are added step by step during the Phase 1 migration
(docs/proposals/phase1-migration-plan.md); until then the registry exposes the
repositories directly.
"""

from dataclasses import dataclass
from functools import lru_cache

from quantcore.gateways.polygon_gateway import PolygonGateway
from quantcore.gateways.yfinance_gateway import YFinanceGateway
from quantcore.repositories.fundamentals_repository import FundamentalsRepository
from quantcore.repositories.harvester_repository import HarvesterPlanDB
from quantcore.repositories.news_repository import NewsStore
from quantcore.repositories.ohlcv_repository import OhlcvRepository
from quantcore.repositories.options_position_repository import OptionsPositionStore
from quantcore.repositories.options_repository import OptionsStore
from quantcore.repositories.portfolio_repository import PortfolioRepository
from quantcore.repositories.sentiment_repository import SentimentStore
from quantcore.services.fundamentals import FundamentalsService
from quantcore.services.harvester import HarvesterService
from quantcore.services.microstructure import MicrostructureService
from quantcore.services.options import OptionsService
from quantcore.services.portfolio import PortfolioService
from quantcore.services.prices import PricesService
from quantcore.services.recommendations import RecommendationsService
from quantcore.services.sentiment import SentimentService


@dataclass(frozen=True)
class Services:
    # Gateways
    yfinance_gateway: YFinanceGateway
    polygon_gateway: PolygonGateway
    # Repositories (wired Step 0). Service objects are added in Steps 1-8.
    ohlcv_repository: OhlcvRepository
    options_repository: OptionsStore
    options_position_repository: OptionsPositionStore
    news_repository: NewsStore
    sentiment_repository: SentimentStore
    fundamentals_repository: FundamentalsRepository
    harvester_repository: HarvesterPlanDB
    portfolio_repository: PortfolioRepository
    # Services
    microstructure: MicrostructureService
    sentiment: SentimentService
    fundamentals: FundamentalsService
    prices: PricesService
    options: OptionsService
    harvester: HarvesterService
    portfolio: PortfolioService
    recommendations: RecommendationsService


@lru_cache(maxsize=1)
def get_services() -> Services:
    yfinance_gateway = YFinanceGateway()
    polygon_gateway = PolygonGateway()
    ohlcv_repository = OhlcvRepository()
    news_repository = NewsStore()
    sentiment_repository = SentimentStore()
    fundamentals_repository = FundamentalsRepository()
    options_repository = OptionsStore()
    harvester_repository = HarvesterPlanDB()
    portfolio_repository = PortfolioRepository()
    # PricesService is constructed first: OptionsService composes it for the
    # ATM-snapshot refresh path (acyclic — Prices never references Options).
    prices = PricesService(
        ohlcv_repository=ohlcv_repository,
        yfinance_gateway=yfinance_gateway,
        options_repository=options_repository,
        sentiment_repository=sentiment_repository,
    )
    options = OptionsService(
        ohlcv_repository=ohlcv_repository,
        yfinance_gateway=yfinance_gateway,
        options_repository=options_repository,
        polygon_gateway=polygon_gateway,
        prices=prices,
    )
    # The leaf domain services are hoisted to locals so RecommendationsService
    # (the cross-domain synthesis layer) can compose the live instances.
    microstructure = MicrostructureService(
        ohlcv_repository=ohlcv_repository,
        yfinance_gateway=yfinance_gateway,
    )
    sentiment = SentimentService(
        news_repository=news_repository,
        sentiment_repository=sentiment_repository,
        yfinance_gateway=yfinance_gateway,
    )
    fundamentals = FundamentalsService(
        fundamentals_repository=fundamentals_repository,
        yfinance_gateway=yfinance_gateway,
    )
    return Services(
        yfinance_gateway=yfinance_gateway,
        polygon_gateway=polygon_gateway,
        ohlcv_repository=ohlcv_repository,
        options_repository=options_repository,
        options_position_repository=OptionsPositionStore(),
        news_repository=news_repository,
        sentiment_repository=sentiment_repository,
        fundamentals_repository=fundamentals_repository,
        harvester_repository=harvester_repository,
        portfolio_repository=portfolio_repository,
        microstructure=microstructure,
        sentiment=sentiment,
        fundamentals=fundamentals,
        prices=prices,
        options=options,
        harvester=HarvesterService(harvester_repository=harvester_repository),
        portfolio=PortfolioService(portfolio_repository=portfolio_repository),
        recommendations=RecommendationsService(
            prices=prices,
            options=options,
            microstructure=microstructure,
            sentiment=sentiment,
            fundamentals=fundamentals,
            ohlcv_repository=ohlcv_repository,
            yfinance_gateway=yfinance_gateway,
        ),
    )
