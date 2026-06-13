"""Transitional shim — module moved to quantcore/repositories/news_repository.py
(architectural standard v2, Phase 1 Step 0). Import from
quantcore.repositories.news_repository instead. This shim is deleted in Step 10.
"""

from quantcore.repositories.news_repository import *  # noqa: F401,F403
from quantcore.repositories.news_repository import NewsStore  # noqa: F401
