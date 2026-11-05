"""Deterministic canonical-fund reconciliation for verified TIGZIG schemes."""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from database.migrate import verify_target
from .config import DatabaseConfig, PROJECT_ROOT
from .importer import _json_safe


MATCHER_VERSION = 'amc-category-isin-v1'
PERFORMANCE_MAPPING_VERSION = 'sif360-regular-growth-v1'
SIF360_REFERENCE_FUND_NAMES = {
    1: 'Magnum Hybrid Long-Short Fund',
    2: 'qsif Equity Long-Short Fund',
    3: 'qsif Hybrid Long-Short Fund',
    4: 'qsif Equity Ex-Top 100 Long-Short Fund',
    5: 'Altiva Hybrid Long-Short Fund',
    6: 'Diviniti Equity Long-Short Fund',
    7: 'Titanium Hybrid Long-Short Fund',
    8: 'Arudha Hybrid Long-Short Fund',
    9: 'iSIF Hybrid Long-Short Fund',
    10: 'iSIF Equity Ex-Top 100 Long-Short Fund',
    11: 'DynaSIF Equity Long-Short Fund',
    12: 'DynaSIF Active Asset Allocator Long-Short Fund',
    13: 'Arudha Equity Long-Short Fund',
    14: 'Apex Hybrid Long-Short Fund',
    15: 'Sapphire Equity Long-Short Fund',
    16: 'WSIF Equity Long-Short Fund',
    17: 'WSIF Equity Ex-Top 100 Long-Short Fund',
    18: 'Titanium Equity Long-Short Fund',
    19: 'qsif Sector Rotation Long-Short Fund',
    20: 'Arthaya Equity Long-Short Fund',
    21: 'qsif Active Asset Allocator Long-Short Fund',
    22: 'Altiva Equity Ex-Top 100 Long-Short Fund',
    23: 'Platinum Hybrid Long-Short Fund',
    24: 'iSIF Equity Long-Short Fund',
    25: 'iSIF Active Asset Allocator Long-Short Fund',
    26: 'RedHex Hybrid Long-Short Fund',
    27: 'DynaSIF Equity Ex-Top 100 Long-Short Fund',
    28: 'Infinity Hybrid Long-Short Fund',
    29: 'Prism Hybrid Long-Short Fund',
    30: 'SUMMIT Equity Long-Short Fund',
    31: 'Magnum Equity Ex-Top 100 Long-Short Fund',
    32: 'Apex Equity Long-Short Fund',
    33: 'Apex Equity Ex-Top 100 Long-Short Fund',
    34: 'Altiva Equity Long-Short Fund',
}
TOKEN_ALIASES = {'actice': 'active', 'regularplan': 'regular'}
VARIANT_TOKENS = {
    'direct', 'regular', 'plan', 'growth', 'option', 'idcw', 'payout', 'of',
    'reinvestment', 'income', 'distribution', 'cum', 'capital', 'withdrawal',
    'annual', 'monthly', 'quarterly', 'half', 'yearly', 'fortnightly', 'both',
    'fund', 'sif',
}


def normalize_name(value: str) -> str:
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def slugify(value: str) -> str:
    return normalize_name(value).replace(' ', '-')


def canonical_brand(amc_name: str) -> str:
    return re.sub(r'\s+SIF$', '', amc_name, flags=re.IGNORECASE).strip()


def normalized_scheme_family(scheme_name: str, amc_name: str) -> str:
    """Remove exact provider variant terms; aliases are explicit source errata."""
    brand_tokens = set(normalize_name(canonical_brand(amc_name)).split())
    tokens = (TOKEN_ALIASES.get(token, token) for token in normalize_name(scheme_name).split())
    return ' '.join(token for token in tokens
                    if token not in brand_tokens and token not in VARIANT_TOKENS)


def normalized_category_family(category_sub: str) -> str:
    return ' '.join(token for token in normalize_name(category_sub).split()
                    if token not in {'fund', 'sif'})


@dataclass(frozen=True)
class Scheme:
    source: str
    scheme_code: int
    fund_house_id: int
    amc_name: str
    scheme_name: str
    category_sub: str | None
    scheme_plan: str | None
    scheme_option: str | None
    idcw_mode: str | None
    idcw_frequency: str | None
    isins: tuple[str, ...]


def _load_schemes(connection: psycopg.Connection) -> list[Scheme]:
    rows = connection.execute("""
        SELECT s.source, s.scheme_code, s.fund_house_id, f.original_amc_name,
               s.scheme_name, s.category_sub, s.scheme_plan, s.scheme_option,
               s.temp_idcw_mode, s.temp_idcw_freq,
               array_remove(array_agg(i.isin ORDER BY i.identifier_type), NULL)
        FROM public.tigzig_schemes s
        JOIN public.tigzig_fund_houses f USING (fund_house_id)
        LEFT JOIN public.tigzig_scheme_isins i USING (source, scheme_code)
        GROUP BY s.source, s.scheme_code, s.fund_house_id, f.original_amc_name,
                 s.scheme_name, s.category_sub, s.scheme_plan, s.scheme_option,
                 s.temp_idcw_mode, s.temp_idcw_freq
        ORDER BY s.source, s.scheme_code
    """).fetchall()
    return [Scheme(*row[:-1], tuple(row[-1])) for row in rows]


def classify_schemes(schemes: list[Scheme]):
    candidates: dict[tuple[int, str], list[Scheme]] = defaultdict(list)
    reviews: list[tuple[Scheme, str, dict[str, Any]]] = []
    raw_by_normalized: dict[tuple[int, str], set[str]] = defaultdict(set)
    for scheme in schemes:
        evidence = {
            'amc': scheme.amc_name, 'scheme_name': scheme.scheme_name,
            'category_sub': scheme.category_sub, 'plan': scheme.scheme_plan,
            'option': scheme.scheme_option, 'isins': list(scheme.isins),
        }
        if not scheme.category_sub:
            reviews.append((scheme, 'missing_fund_family', evidence))
            continue
        if scheme.scheme_plan not in {'Direct', 'Regular'} or scheme.scheme_option not in {'Growth', 'IDCW'}:
            reviews.append((scheme, 'unsupported_variant', evidence))
            continue
        if not scheme.isins:
            reviews.append((scheme, 'missing_verified_identifier', evidence))
            continue
        normalized = normalized_category_family(scheme.category_sub)
        scheme_family = normalized_scheme_family(scheme.scheme_name, scheme.amc_name)
        evidence['normalized_category_family'] = normalized
        evidence['normalized_scheme_family'] = scheme_family
        if scheme_family != normalized:
            reviews.append((scheme, 'scheme_family_mismatch', evidence))
            continue
        raw_by_normalized[(scheme.fund_house_id, normalized)].add(scheme.category_sub)
        candidates[(scheme.fund_house_id, normalized)].append(scheme)
    ambiguous_keys = {key for key, raw in raw_by_normalized.items() if len(raw) > 1}
    groups = {}
    for key, members in candidates.items():
        if key in ambiguous_keys:
            for scheme in members:
                reviews.append((scheme, 'conflicting_fund_family_labels', {
                    'labels': sorted(raw_by_normalized[key]), 'amc': scheme.amc_name,
                }))
        else:
            groups[key] = members
    return groups, reviews


def reconcile_funds(connection: psycopg.Connection) -> dict[str, Any]:
    verify_target(connection)
    connection.execute('SELECT pg_advisory_xact_lock(%s, %s)', (861943, 6))
    schemes = _load_schemes(connection)
    groups, reviews = classify_schemes(schemes)
    fund_ids: dict[tuple[int, str], int] = {}
    mapped = 0
    identifier_count = 0

    for (fund_house_id, family_key), members in sorted(groups.items(), key=lambda item: (item[1][0].amc_name, item[0][1])):
        exemplar = members[0]
        family_name = exemplar.category_sub or family_key
        canonical_name = f'{canonical_brand(exemplar.amc_name)} {family_name}'
        slug = slugify(canonical_name)
        fund_id = connection.execute("""
            INSERT INTO public.app_funds(canonical_name, slug, status)
            VALUES (%s, %s, 'active')
            ON CONFLICT (slug) DO UPDATE SET
              canonical_name=excluded.canonical_name, status='active', updated_at=now()
            WHERE (app_funds.canonical_name, app_funds.status)
                  IS DISTINCT FROM (excluded.canonical_name, excluded.status)
            RETURNING fund_id
        """, (canonical_name, slug)).fetchone()
        if fund_id is None:
            fund_id = connection.execute(
                'SELECT fund_id FROM public.app_funds WHERE slug=%s', (slug,)
            ).fetchone()
        fund_ids[(fund_house_id, family_key)] = fund_id[0]

        for scheme in members:
            evidence = {
                'matcher_version': MATCHER_VERSION,
                'fund_house_id': scheme.fund_house_id,
                'amc_exact': scheme.amc_name,
                'family_exact': scheme.category_sub,
                'family_normalized': family_key,
                'scheme_name_normalized': normalize_name(scheme.scheme_name),
                'scheme_family_normalized': normalized_scheme_family(
                    scheme.scheme_name, scheme.amc_name
                ),
                'plan': scheme.scheme_plan,
                'option': scheme.scheme_option,
                'verified_identifiers': list(scheme.isins),
            }
            connection.execute("""
                INSERT INTO public.app_tigzig_fund_mapping(
                  fund_id, source, scheme_code, verification_method,
                  verification_evidence, verified_by, verified_at)
                VALUES (%s,%s,%s,%s,%s,%s,now())
                ON CONFLICT (source, scheme_code) DO UPDATE SET
                  fund_id=excluded.fund_id,
                  verification_method=excluded.verification_method,
                  verification_evidence=excluded.verification_evidence,
                  verified_by=excluded.verified_by, verified_at=now(), updated_at=now()
                WHERE (app_tigzig_fund_mapping.fund_id,
                       app_tigzig_fund_mapping.verification_method,
                       app_tigzig_fund_mapping.verification_evidence,
                       app_tigzig_fund_mapping.verified_by)
                  IS DISTINCT FROM
                      (excluded.fund_id, excluded.verification_method,
                       excluded.verification_evidence, excluded.verified_by)
            """, (fund_id[0], scheme.source, scheme.scheme_code, MATCHER_VERSION,
                    Jsonb(evidence), MATCHER_VERSION))
            mapped += 1
            for position, isin in enumerate(scheme.isins):
                namespace = 'ISIN_PRIMARY' if position == 0 else 'ISIN_SECONDARY'
                connection.execute("""
                    INSERT INTO public.app_external_identifiers(
                      fund_id, namespace, identifier_value, source,
                      source_scheme_code, verified, verification_evidence)
                    VALUES (%s,%s,%s,%s,%s,true,%s)
                    ON CONFLICT (namespace, identifier_value) DO UPDATE SET
                      fund_id=excluded.fund_id, source=excluded.source,
                      source_scheme_code=excluded.source_scheme_code,
                      verified=true, verification_evidence=excluded.verification_evidence,
                      updated_at=now()
                    WHERE (app_external_identifiers.fund_id,
                           app_external_identifiers.source,
                           app_external_identifiers.source_scheme_code,
                           app_external_identifiers.verified,
                           app_external_identifiers.verification_evidence)
                      IS DISTINCT FROM
                          (excluded.fund_id, excluded.source,
                           excluded.source_scheme_code, excluded.verified,
                           excluded.verification_evidence)
                """, (fund_id[0], namespace, isin, scheme.source, scheme.scheme_code,
                        Jsonb({'matcher_version': MATCHER_VERSION, 'scheme_code': scheme.scheme_code})))
                identifier_count += 1

    for scheme, reason, evidence in reviews:
        key = normalized_category_family(scheme.category_sub) if scheme.category_sub else None
        connection.execute("""
            INSERT INTO public.app_tigzig_mapping_reviews(
              source, scheme_code, proposed_fund_key, reason_code, evidence)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (source, scheme_code) DO UPDATE SET
              proposed_fund_key=excluded.proposed_fund_key,
              reason_code=excluded.reason_code, evidence=excluded.evidence,
              status='pending', reviewed_by=NULL, reviewed_at=NULL, updated_at=now()
        """, (scheme.source, scheme.scheme_code, key, reason, Jsonb(evidence)))

    ordered_funds = connection.execute(
        "SELECT fund_id FROM public.app_funds WHERE status='active' ORDER BY canonical_name, fund_id"
    ).fetchall()
    display_order = {row[0]: index for index, row in enumerate(ordered_funds, 1)}
    for key, members in groups.items():
        fund_id = fund_ids[key]
        preferred = min(members, key=lambda scheme: (
            0 if (scheme.scheme_plan, scheme.scheme_option) == ('Direct', 'Growth') else
            1 if (scheme.scheme_plan, scheme.scheme_option) == ('Regular', 'Growth') else
            2 if scheme.scheme_plan == 'Direct' else 3,
            scheme.scheme_code,
        ))
        selection_evidence = {
            'rule': 'direct-growth-then-regular-growth-then-idcw',
            'matcher_version': MATCHER_VERSION,
            'plan': preferred.scheme_plan, 'option': preferred.scheme_option,
        }
        connection.execute("""
            INSERT INTO public.app_ticker_config(
              fund_id, preferred_source, preferred_scheme_code, display_order,
              is_visible, selection_verified, selection_evidence, verified_by, verified_at)
            VALUES (%s,%s,%s,%s,true,true,%s,%s,now())
            ON CONFLICT (fund_id) DO UPDATE SET
              preferred_source=excluded.preferred_source,
              preferred_scheme_code=excluded.preferred_scheme_code,
              display_order=excluded.display_order, is_visible=excluded.is_visible,
              selection_verified=excluded.selection_verified,
              selection_evidence=excluded.selection_evidence,
              verified_by=excluded.verified_by, verified_at=now(), updated_at=now()
            WHERE (app_ticker_config.preferred_source,
                   app_ticker_config.preferred_scheme_code,
                   app_ticker_config.display_order,
                   app_ticker_config.is_visible,
                   app_ticker_config.selection_verified,
                   app_ticker_config.selection_evidence,
                   app_ticker_config.verified_by)
              IS DISTINCT FROM
                  (excluded.preferred_source, excluded.preferred_scheme_code,
                   excluded.display_order, excluded.is_visible,
                   excluded.selection_verified, excluded.selection_evidence,
                   excluded.verified_by)
        """, (fund_id, preferred.source, preferred.scheme_code,
                display_order[fund_id], Jsonb(selection_evidence), MATCHER_VERSION))

    unresolved_performance_mappings = []
    for reference_fund_id, canonical_name in SIF360_REFERENCE_FUND_NAMES.items():
        candidates = connection.execute("""
            SELECT f.fund_id,s.source,s.scheme_code,s.scheme_name,
                   array_remove(array_agg(i.isin ORDER BY i.identifier_type),NULL)
            FROM public.app_funds f
            JOIN public.app_tigzig_fund_mapping m USING(fund_id)
            JOIN public.tigzig_schemes s USING(source,scheme_code)
            LEFT JOIN public.tigzig_scheme_isins i USING(source,scheme_code)
            WHERE f.canonical_name=%s AND f.status='active'
              AND s.scheme_plan='Regular' AND s.scheme_option='Growth'
            GROUP BY f.fund_id,s.source,s.scheme_code,s.scheme_name
            ORDER BY s.source,s.scheme_code
        """, (canonical_name,)).fetchall()
        if len(candidates) != 1:
            unresolved_performance_mappings.append({
                'reference_fund_id': reference_fund_id,
                'canonical_name': canonical_name,
                'candidate_count': len(candidates),
            })
            continue
        fund_id, source, scheme_code, scheme_name, isins = candidates[0]
        evidence = {
            'mapping_version': PERFORMANCE_MAPPING_VERSION,
            'reference_fund_id': reference_fund_id,
            'canonical_name_exact': canonical_name,
            'scheme_name': scheme_name,
            'plan': 'Regular',
            'option': 'Growth',
            'verified_identifiers': isins,
            'basis': 'reference-response metrics plus canonical identity and ISIN',
        }
        connection.execute("""
            INSERT INTO public.app_performance_config(
              reference_fund_id,fund_id,preferred_source,preferred_scheme_code,
              mapping_confidence,selection_verified,selection_evidence,
              verified_by,verified_at)
            VALUES (%s,%s,%s,%s,'high',true,%s,%s,now())
            ON CONFLICT (reference_fund_id) DO UPDATE SET
              fund_id=excluded.fund_id,
              preferred_source=excluded.preferred_source,
              preferred_scheme_code=excluded.preferred_scheme_code,
              mapping_confidence=excluded.mapping_confidence,
              selection_verified=excluded.selection_verified,
              selection_evidence=excluded.selection_evidence,
              verified_by=excluded.verified_by,
              verified_at=now(),updated_at=now()
            WHERE (app_performance_config.fund_id,
                   app_performance_config.preferred_source,
                   app_performance_config.preferred_scheme_code,
                   app_performance_config.mapping_confidence,
                   app_performance_config.selection_verified,
                   app_performance_config.selection_evidence,
                   app_performance_config.verified_by)
              IS DISTINCT FROM
                  (excluded.fund_id,excluded.preferred_source,
                   excluded.preferred_scheme_code,excluded.mapping_confidence,
                   excluded.selection_verified,excluded.selection_evidence,
                   excluded.verified_by)
        """, (reference_fund_id, fund_id, source, scheme_code,
                Jsonb(evidence), PERFORMANCE_MAPPING_VERSION))

    counts = connection.execute("""
      SELECT (SELECT count(*)::integer FROM public.app_funds),
             (SELECT count(*)::integer FROM public.app_tigzig_fund_mapping),
             (SELECT count(*)::integer FROM public.app_ticker_config),
             (SELECT count(*)::integer FROM public.app_performance_config),
             (SELECT count(*)::integer FROM public.app_external_identifiers),
             (SELECT count(*)::integer FROM public.app_tigzig_mapping_reviews WHERE status='pending')
    """).fetchone()
    return {
        'matcher_version': MATCHER_VERSION, 'source_schemes': len(schemes),
        'deterministic_groups': len(groups), 'mapped_this_run': mapped,
        'identifiers_seen': identifier_count, 'review_candidates_this_run': len(reviews),
        'unresolved_performance_mappings': unresolved_performance_mappings,
        'database_counts': dict(zip(
            ('funds', 'mappings', 'ticker_configs', 'performance_configs',
             'external_identifiers', 'pending_reviews'), counts
        )),
    }


def run(output: Path | None = None) -> dict[str, Any]:
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
        result = reconcile_funds(connection)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(_json_safe(result), indent=2), encoding='utf-8')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=PROJECT_ROOT / 'docs' / 'reports' / 'milestone-6-reconciliation.json')
    arguments = parser.parse_args()
    try:
        print(json.dumps(_json_safe(run(arguments.output)), indent=2))
        return 0
    except Exception as error:
        print(f'Fund reconciliation failed ({type(error).__name__})')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
