"""Deterministic AMC, strategy, and scheme reconciliation for the SIF domain."""
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

import psycopg
from psycopg import sql

from database.migrate import verify_target
from .config import DatabaseConfig
from .fund_registry import FUND_HOUSES, TIGZIG_TO_AMC, normalize_name, slugify

LOG = logging.getLogger('factsheet-strategy-manager')

MATCHER_VERSION = 'sif-factsheet-reconciliation-v1'


def strategy_category_from_name(fund_name: str) -> tuple[str, str]:
    """Infer high-level strategy category and subcategory from canonical fund name."""
    lower = fund_name.lower()
    if 'hybrid' in lower:
        return 'Hybrid', 'Hybrid Long-Short'
    if 'active asset allocator' in lower:
        return 'Hybrid', 'Active Asset Allocator'
    if 'sector rotation' in lower:
        return 'Equity', 'Sector Rotation'
    if 'ex-top 100' in lower:
        return 'Equity', 'Equity Ex-Top 100'
    if 'equity' in lower:
        return 'Equity', 'Equity Long-Short'
    return 'Other', 'Long-Short'


@dataclass
class IdentitySyncSummary:
    amcs_synced: int
    aliases_synced: int
    strategies_synced: int
    schemes_synced: int
    scheme_links_synced: int


class StrategyManager:
    """Synchronizes registered AMCs, canonical strategies, schemes, and links in public SIF tables."""

    def __init__(self, connection: psycopg.Connection):
        self.connection = connection
        verify_target(self.connection)

    def sync_amcs(self) -> dict[str, int]:
        """Ensure all 17 registered AMCs exist in sif_amcs and record aliases."""
        amc_ids: dict[str, int] = {}
        # 1. Insert official AMCs
        for house in sorted(set(FUND_HOUSES.values())):
            row = self.connection.execute("""
                INSERT INTO public.sif_amcs(amc_name)
                VALUES (%s)
                ON CONFLICT (amc_name) DO UPDATE SET updated_at = NOW()
                RETURNING amc_id
            """, (house,)).fetchone()
            amc_id = row[0]
            amc_ids[house] = amc_id

            # Self-alias for OFFICIAL_FACTSHEET
            self.connection.execute("""
                INSERT INTO public.sif_amc_aliases(
                    amc_id, source_system, reported_name, verified, verified_at
                ) VALUES (%s, 'OFFICIAL_FACTSHEET', %s, TRUE, NOW())
                ON CONFLICT (source_system, reported_name) DO UPDATE SET
                    amc_id = EXCLUDED.amc_id, verified = TRUE, verified_at = NOW()
            """, (amc_id, house))

        # 2. Record TIGZIG aliases
        for tigzig_brand, official_amc in TIGZIG_TO_AMC.items():
            amc_id = amc_ids.get(official_amc)
            if amc_id:
                self.connection.execute("""
                    INSERT INTO public.sif_amc_aliases(
                        amc_id, source_system, reported_name, verified, verified_at
                    ) VALUES (%s, 'TIGZIG', %s, TRUE, NOW())
                    ON CONFLICT (source_system, reported_name) DO UPDATE SET
                        amc_id = EXCLUDED.amc_id, verified = TRUE, verified_at = NOW()
                """, (amc_id, tigzig_brand))

        return amc_ids

    def sync_strategies(self, amc_ids: dict[str, int] | None = None) -> dict[str, int]:
        """Ensure all 34 canonical strategies exist in sif_strategies."""
        if amc_ids is None:
            amc_ids = self.sync_amcs()

        strategy_ids: dict[str, int] = {}
        for strategy_name, house_name in FUND_HOUSES.items():
            amc_id = amc_ids.get(house_name)
            if not amc_id:
                raise RuntimeError(f'No AMC found for {house_name}')

            slug = slugify(strategy_name)
            cat, subcat = strategy_category_from_name(strategy_name)

            row = self.connection.execute("""
                INSERT INTO public.sif_strategies(
                    amc_id, strategy_name, slug, strategy_category,
                    strategy_subcategory, structure_type, is_active, website_visible
                ) VALUES (%s, %s, %s, %s, %s, 'Specified Investment Fund', TRUE, TRUE)
                ON CONFLICT (amc_id, slug) DO UPDATE SET
                    strategy_name = EXCLUDED.strategy_name,
                    strategy_category = EXCLUDED.strategy_category,
                    strategy_subcategory = EXCLUDED.strategy_subcategory,
                    is_active = TRUE,
                    website_visible = TRUE,
                    updated_at = NOW()
                RETURNING strategy_id
            """, (amc_id, strategy_name, slug, cat, subcat)).fetchone()
            strategy_ids[strategy_name] = row[0]

        return strategy_ids

    def sync_schemes_from_tigzig(self, amc_ids: dict[str, int] | None = None) -> int:
        """Sync scheme records from tigzig_schemes into sif_schemes with AMC attribution."""
        if amc_ids is None:
            amc_ids = self.sync_amcs()

        # Build map from tigzig fund_house_id to sif amc_id
        house_rows = self.connection.execute("""
            SELECT fund_house_id, original_amc_name FROM public.tigzig_fund_houses
        """).fetchall()
        house_to_amc: dict[int, int] = {}
        for fund_house_id, brand_name in house_rows:
            official_name = TIGZIG_TO_AMC.get(brand_name)
            if official_name and official_name in amc_ids:
                house_to_amc[fund_house_id] = amc_ids[official_name]

        # Insert / update into sif_schemes
        schemes = self.connection.execute("""
            SELECT scheme_code, temp_sif_code, fund_house_id, scheme_name,
                   scheme_type, category, category_sub, category_group_clean,
                   category_group, scheme_plan, scheme_option, first_nav_date,
                   last_nav_date, is_active, is_stale, txic_code, source,
                   temp_plan_basis, temp_idcw_mode, temp_idcw_freq, temp_n_nav_days
            FROM public.tigzig_schemes
        """).fetchall()

        synced = 0
        for s in schemes:
            amc_id = house_to_amc.get(s[2])
            if not amc_id:
                continue
            self.connection.execute("""
                INSERT INTO public.sif_schemes(
                    scheme_code, temp_sif_code, amc_id, scheme_name,
                    scheme_type, category, category_sub, category_group_clean,
                    category_group, scheme_plan, scheme_option, first_date,
                    last_date, is_active, is_stale, txic_code, source,
                    temp_plan_basis, temp_idcw_mode, temp_idcw_freq, temp_n_nav_days
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (scheme_code) DO UPDATE SET
                    temp_sif_code = EXCLUDED.temp_sif_code,
                    amc_id = EXCLUDED.amc_id,
                    scheme_name = EXCLUDED.scheme_name,
                    scheme_type = EXCLUDED.scheme_type,
                    category = EXCLUDED.category,
                    category_sub = EXCLUDED.category_sub,
                    category_group_clean = EXCLUDED.category_group_clean,
                    category_group = EXCLUDED.category_group,
                    scheme_plan = EXCLUDED.scheme_plan,
                    scheme_option = EXCLUDED.scheme_option,
                    first_date = EXCLUDED.first_date,
                    last_date = EXCLUDED.last_date,
                    is_active = EXCLUDED.is_active,
                    is_stale = EXCLUDED.is_stale,
                    txic_code = EXCLUDED.txic_code,
                    source = EXCLUDED.source,
                    temp_plan_basis = EXCLUDED.temp_plan_basis,
                    temp_idcw_mode = EXCLUDED.temp_idcw_mode,
                    temp_idcw_freq = EXCLUDED.temp_idcw_freq,
                    temp_n_nav_days = EXCLUDED.temp_n_nav_days,
                    updated_at = NOW()
            """, (s[0], s[1], amc_id, s[3], s[4], s[5], s[6], s[7], s[8], s[9], s[10],
                  s[11], s[12], s[13], s[14], s[15], s[16], s[17], s[18], s[19], s[20]))
            synced += 1

        # Also copy ISINs for synchronized schemes
        self.connection.execute("""
            INSERT INTO public.sif_scheme_isins(scheme_code, isin, isin_type)
            SELECT tsi.scheme_code, tsi.isin, tsi.identifier_type
            FROM public.tigzig_scheme_isins tsi
            JOIN public.sif_schemes ss ON ss.scheme_code = tsi.scheme_code
            WHERE NOT EXISTS (
                SELECT 1 FROM public.sif_scheme_isins ssi
                WHERE ssi.scheme_code = tsi.scheme_code
                  AND (ssi.isin = tsi.isin OR ssi.isin_type = tsi.identifier_type)
            )
        """)

        return synced

    def link_strategies_and_schemes(self, strategy_ids: dict[str, int] | None = None) -> int:
        """Establish verified sif_strategy_scheme_links using verified app_tigzig_fund_mapping."""
        if strategy_ids is None:
            strategy_ids = self.sync_strategies()

        # Query app_tigzig_fund_mapping joined with app_funds
        rows = self.connection.execute("""
            SELECT m.scheme_code, f.canonical_name
            FROM public.app_tigzig_fund_mapping m
            JOIN public.app_funds f USING (fund_id)
        """).fetchall()

        linked = 0
        now = datetime.now(timezone.utc)
        for scheme_code, canonical_name in rows:
            # Case-insensitive / normalized lookup for strategy_id
            target_strategy_id = None
            for sname, sid in strategy_ids.items():
                if normalize_name(sname) == normalize_name(canonical_name):
                    target_strategy_id = sid
                    break

            if not target_strategy_id:
                continue

            scheme_amc = self.connection.execute(
                "SELECT amc_id FROM public.sif_schemes WHERE scheme_code = %s", (scheme_code,)
            ).fetchone()
            strat_amc = self.connection.execute(
                "SELECT amc_id FROM public.sif_strategies WHERE strategy_id = %s", (target_strategy_id,)
            ).fetchone()

            if scheme_amc and strat_amc and scheme_amc[0] != strat_amc[0]:
                self.connection.execute(
                    "UPDATE public.sif_schemes SET amc_id = %s WHERE scheme_code = %s",
                    (strat_amc[0], scheme_code)
                )

            self.connection.execute("""
                INSERT INTO public.sif_strategy_scheme_links(
                    scheme_code, strategy_id, mapping_method, review_status,
                    reviewed_by, reviewed_at
                ) VALUES (%s, %s, 'EXTERNAL_ID', 'VERIFIED', %s, %s)
                ON CONFLICT (scheme_code) DO UPDATE SET
                    strategy_id = EXCLUDED.strategy_id,
                    mapping_method = EXCLUDED.mapping_method,
                    review_status = 'VERIFIED',
                    reviewed_by = EXCLUDED.reviewed_by,
                    reviewed_at = EXCLUDED.reviewed_at
            """, (scheme_code, target_strategy_id, MATCHER_VERSION, now))
            linked += 1

        return linked

    def sync_all(self) -> IdentitySyncSummary:
        """Run complete synchronization of AMCs, strategies, schemes, and links."""
        amc_ids = self.sync_amcs()
        strat_ids = self.sync_strategies(amc_ids)
        schemes_count = self.sync_schemes_from_tigzig(amc_ids)
        links_count = self.link_strategies_and_schemes(strat_ids)

        return IdentitySyncSummary(
            amcs_synced=len(amc_ids),
            aliases_synced=len(TIGZIG_TO_AMC) + len(amc_ids),
            strategies_synced=len(strat_ids),
            schemes_synced=schemes_count,
            scheme_links_synced=links_count,
        )


def sync_sif_identities(db_config: DatabaseConfig | None = None) -> IdentitySyncSummary:
    cfg = db_config or DatabaseConfig.from_env()
    with psycopg.connect(**cfg.connection_kwargs(), autocommit=True) as conn:
        with conn.transaction():
            manager = StrategyManager(conn)
            summary = manager.sync_all()
            LOG.info("Identity sync complete: %s", summary)
            return summary
