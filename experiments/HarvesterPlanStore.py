"""Compatibility shim — Harvester persistence/logic moved in Phase 1 Step 6.

The Harvester SQL/persistence now lives in
``quantcore.repositories.harvester_repository`` (``HarvesterPlanDB``,
``PlanBuildParams``, the ``SQL_*`` constants, ``fetch_daily_history_ohlcv``),
and the former ``HarvesterController`` orchestration is
``quantcore.services.harvester.HarvesterService``.

This module re-exports those names so any lingering
``from experiments.HarvesterPlanStore import ...`` keeps working. It is slated
for deletion in Phase 1 Step 10 (docs/proposals/phase1-migration-plan.md).
"""

from quantcore.repositories import harvester_repository as _repo
from quantcore.repositories.harvester_repository import (  # noqa: F401
    HarvesterPlanDB,
    PlanBuildParams,
    fetch_daily_history_ohlcv,
    _utc_now_iso,
)
from quantcore.services.harvester import HarvesterService as _HarvesterService

# Re-export every module-level SQL_* constant from the repository.
for _name in dir(_repo):
    if _name.startswith("SQL_"):
        globals()[_name] = getattr(_repo, _name)
del _name


class HarvesterController(_HarvesterService):
    """Back-compat alias: HarvesterController(db) -> HarvesterService(repo=db)."""

    def __init__(self, db: HarvesterPlanDB):
        super().__init__(harvester_repository=db)
        self.db = db
