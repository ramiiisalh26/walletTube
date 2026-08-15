"""
MVP analytics metrics — async functions returning plain numbers/dicts.
Used by admin views; no HTTP endpoint is wired up here.

All three metrics are designed to work even when search_history has only a
few hundred rows (no MATERIALIZED VIEW dependency).
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def weekly_retention(session: AsyncSession) -> float:
    """
    Return the % of registered users whose second search happened within
    7–14 days of their first.

    Only cohorts whose first search was at least 14 days ago are counted —
    newer users haven't had enough time to 'retain'.
    Returns 0.0 when the cohort is empty.
    """
    row = (await session.execute(text("""
        WITH first_searches AS (
            -- One row per user: their earliest search timestamp
            SELECT
                user_id,
                MIN(created_at) AS first_at
            FROM search_history
            WHERE user_id IS NOT NULL
            GROUP BY user_id
            -- Only count cohorts old enough to have had a chance to return
            HAVING MIN(created_at) <= NOW() - INTERVAL '14 days'
        ),
        retained AS (
            -- Users who searched again in days 7–14 after their first search
            SELECT DISTINCT fs.user_id
            FROM first_searches fs
            JOIN search_history sh ON sh.user_id = fs.user_id
            WHERE sh.created_at >= fs.first_at + INTERVAL '7 days'
              AND sh.created_at <  fs.first_at + INTERVAL '14 days'
        )
        SELECT
            COUNT(fs.user_id)                                        AS cohort,
            COUNT(r.user_id)                                         AS retained
        FROM  first_searches fs
        LEFT JOIN retained r ON r.user_id = fs.user_id
    """))).fetchone()

    if not row or not row.cohort:
        return 0.0
    return round(row.retained / row.cohort * 100, 2)


async def search_hit_rate(session: AsyncSession, days: int = 7) -> float:
    """
    Return (searches with ≥1 click) / (total searches) × 100 over the last
    `days` days.  Anonymous and logged-in searches are both counted.
    Returns 0.0 when there are no searches in the window.
    """
    row = (await session.execute(
        text("""
            SELECT
                COUNT(DISTINCT sh.id)                                     AS total_searches,
                COUNT(DISTINCT CASE WHEN sc.id IS NOT NULL THEN sh.id END) AS searches_with_click
            FROM  search_history sh
            LEFT JOIN search_clicks sc ON sc.search_id = sh.id
            WHERE sh.created_at >= NOW() - CAST(:days || ' days' AS INTERVAL)
        """),
        {"days": days},
    )).fetchone()

    if not row or not row.total_searches:
        return 0.0
    return round(row.searches_with_click / row.total_searches * 100, 2)


async def free_to_paid_conversion(session: AsyncSession) -> float:
    """
    Return % of free-plan users who hit their daily search limit at least
    once and subsequently upgraded to a paid plan.
    Returns 0.0 when no free-plan user has hit the limit yet.
    """
    row = (await session.execute(text("""
        WITH free_limit_hitters AS (
            -- Free-plan users who used all their daily searches at least once
            SELECT DISTINCT du.user_id
            FROM  daily_usage du
            JOIN  users              u  ON u.id  = du.user_id
            JOIN  subscription_plans sp ON sp.id = u.plan_id
            WHERE sp.slug             = 'free'
              AND du.search_count    >= sp.searches_per_day
        ),
        upgraders AS (
            -- Limit-hitters who now have an active non-free subscription
            SELECT DISTINCT flh.user_id
            FROM  free_limit_hitters   flh
            JOIN  user_subscriptions   us  ON us.user_id  = flh.user_id
            JOIN  subscription_plans   sp2 ON sp2.id      = us.plan_id
            WHERE us.status  = 'active'
              AND sp2.slug  != 'free'
        )
        SELECT
            COUNT(flh.user_id)  AS total_hitters,
            COUNT(u.user_id)    AS upgraded
        FROM  free_limit_hitters flh
        LEFT JOIN upgraders u ON u.user_id = flh.user_id
    """))).fetchone()

    if not row or not row.total_hitters:
        return 0.0
    return round(row.upgraded / row.total_hitters * 100, 2)
