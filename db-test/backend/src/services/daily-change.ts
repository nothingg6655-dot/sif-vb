import { percentageReturn } from './performance.js';

export interface DailyNavRow {
  displayOrder: number;
  fundId: number;
  fullName: string;
  source: string;
  schemeCode: number;
  plan: string | null;
  option: string | null;
  date: string;
  nav: string;
}

export interface DailyTickerItem {
  fullName: string;
  latestNAV: number | null;
  change: number | null;
}

export interface DailyChangeDiagnostic {
  referenceFundId: number;
  fundId: number;
  fullName: string;
  source: string;
  schemeCode: number;
  plan: string | null;
  option: string | null;
  latestDate: string | null;
  latestNav: number | null;
  previousDate: string | null;
  previousNav: number | null;
  calculatedChange: number | null;
  calculationStatus: 'complete' | 'invalid' | 'unavailable';
  dataQualityWarnings: string[];
}

interface DateObservation {
  date: string;
  nav: string | null;
  conflicting: boolean;
}

function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function positiveNumber(value: string | null): number | null {
  if (value === null || !/^\+?\d+(?:\.\d+)?$/.test(value.trim())) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function equivalentNav(first: string, second: string): boolean {
  const result = percentageReturn(first, second, false);
  return result.unrounded === '0';
}

function observationsByDate(rows: DailyNavRow[], warnings: Set<string>): DateObservation[] {
  const grouped = new Map<string, string[]>();
  for (const row of rows) {
    if (!isIsoDate(row.date)) {
      warnings.add('invalid_nav_date_excluded');
      continue;
    }
    const values = grouped.get(row.date) ?? [];
    values.push(row.nav);
    grouped.set(row.date, values);
  }

  return [...grouped.entries()]
    .sort(([left], [right]) => right.localeCompare(left))
    .map(([date, values]) => {
      const validValues = values.filter((value) => positiveNumber(value) !== null)
        .sort((left, right) => left.localeCompare(right));
      if (validValues.length !== values.length) warnings.add('invalid_nav_value');
      if (validValues.length === 0) return { date, nav: null, conflicting: false };
      const unique: string[] = [];
      for (const value of validValues) {
        if (!unique.some((existing) => equivalentNav(existing, value))) unique.push(value);
      }
      if (values.length > 1 && unique.length === 1 && validValues.length === values.length) {
        warnings.add('duplicate_observation_deduplicated');
      }
      if (unique.length > 1) {
        warnings.add('conflicting_duplicate_observation');
        return { date, nav: null, conflicting: true };
      }
      return { date, nav: unique[0]!, conflicting: false };
    });
}

function calculateSeries(rows: DailyNavRow[]): {
  item: DailyTickerItem;
  diagnostic: DailyChangeDiagnostic;
} {
  const orderedRows = [...rows].sort((left, right) =>
    left.date.localeCompare(right.date) || left.nav.localeCompare(right.nav));
  const identity = orderedRows[0]!;
  const warnings = new Set<string>();
  const identities = new Set(orderedRows.map((row) =>
    `${row.fundId}|${row.source}|${row.schemeCode}|${row.plan ?? ''}|${row.option ?? ''}`));
  if (identities.size !== 1) warnings.add('multiple_scheme_series');

  const dated = observationsByDate(orderedRows, warnings);
  const latestIndex = dated.findIndex((observation) =>
    observation.nav !== null && !observation.conflicting);
  if (latestIndex > 0) warnings.add('invalid_latest_observation_excluded');
  if (dated[0]?.conflicting) warnings.add('latest_observation_conflicting');
  if (latestIndex < 0 || identities.size !== 1 || dated[0]?.conflicting) {
    const diagnostic: DailyChangeDiagnostic = {
      referenceFundId: identity.displayOrder,
      fundId: identity.fundId,
      fullName: identity.fullName,
      source: identity.source,
      schemeCode: identity.schemeCode,
      plan: identity.plan,
      option: identity.option,
      latestDate: null,
      latestNav: null,
      previousDate: null,
      previousNav: null,
      calculatedChange: null,
      calculationStatus: 'invalid',
      dataQualityWarnings: [...warnings].sort(),
    };
    return { item: { fullName: identity.fullName, latestNAV: null, change: null }, diagnostic };
  }

  const latest = dated[latestIndex]!;
  const previous = dated[latestIndex + 1];
  const latestNav = positiveNumber(latest.nav);
  let previousNav: number | null = null;
  let change: number | null = null;
  let status: DailyChangeDiagnostic['calculationStatus'] = 'complete';
  if (!previous) {
    warnings.add('previous_observation_missing');
    status = 'unavailable';
  } else if (previous.conflicting) {
    warnings.add('previous_observation_conflicting');
    status = 'invalid';
  } else {
    previousNav = positiveNumber(previous.nav);
    if (previousNav === null) {
      warnings.add('previous_nav_invalid');
      status = 'invalid';
    } else {
      change = percentageReturn(previous.nav!, latest.nav!, false).value;
      if (change === null) {
        warnings.add('daily_change_calculation_failed');
        status = 'invalid';
      }
    }
  }

  const diagnostic: DailyChangeDiagnostic = {
    referenceFundId: identity.displayOrder,
    fundId: identity.fundId,
    fullName: identity.fullName,
    source: identity.source,
    schemeCode: identity.schemeCode,
    plan: identity.plan,
    option: identity.option,
    latestDate: latest.date,
    latestNav,
    previousDate: previous?.date ?? null,
    previousNav,
    calculatedChange: change,
    calculationStatus: status,
    dataQualityWarnings: [...warnings].sort(),
  };
  return { item: { fullName: identity.fullName, latestNAV: latestNav, change }, diagnostic };
}

export function calculateDailyTicker(rows: DailyNavRow[]) {
  const grouped = new Map<number, DailyNavRow[]>();
  for (const row of rows) {
    const group = grouped.get(row.displayOrder) ?? [];
    group.push(row);
    grouped.set(row.displayOrder, group);
  }
  const calculated = [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, series]) => calculateSeries(series));
  return {
    data: calculated.map(({ item }) => item),
    diagnostics: calculated.map(({ diagnostic }) => diagnostic),
  };
}
