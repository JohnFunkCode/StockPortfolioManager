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

from quantcore.gateways.yfinance_gateway import YFinanceGateway
from quantcore.repositories.fundamentals_repository import FundamentalsRepository
from quantcore.repositories.news_repository import NewsStore
from quantcore.repositories.ohlcv_repository import OhlcvRepository
from quantcore.repositories.options_position_repository import OptionsPositionStore
from quantcore.repositories.options_repository import OptionsStore
from quantcore.repositories.sentiment_repository import SentimentStore
from quantcore.services.microstructure import MicrostructureService


@dataclass(frozen=True)
class Services:
    # Gateways
    yfinance_gateway: YFinanceGateway
    # Repositories (wired Step 0). Service objects are added in Steps 1-8.
    ohlcv_repository: OhlcvRepository
    options_repository: OptionsStore
    options_position_repository: OptionsPositionStore
    news_repository: NewsStore
    sentiment_repository: SentimentStore
    fundamentals_repository: FundamentalsRepository
    # Services
    microstructure: MicrostructureService


@lru_cache(maxsize=1)
def get_services() -> Services:
    yfinance_gateway = YFinanceGateway()
    ohlcv_repository = OhlcvRepository()
    return Services(
        yfinance_gateway=yfinance_gateway,
        ohlcv_repository=ohlcv_repository,
        options_repository=OptionsStore(),
        options_position_repository=OptionsPositionStore(),
        news_repository=NewsStore(),
        sentiment_repository=SentimentStore(),
        fundamentals_repository=FundamentalsRepository(),
        microstructure=MicrostructureService(
            ohlcv_repository=ohlcv_repository,
            yfinance_gateway=yfinance_gateway,
        ),
    )
