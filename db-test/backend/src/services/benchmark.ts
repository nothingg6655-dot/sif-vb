export interface BenchmarkObservation {
  date: string;
  value: number;
}

export interface BenchmarkProvider {
  history(identifier: string, fromDate: string, toDate: string): Promise<BenchmarkObservation[]>;
}

export const benchmarkDefinitions = [
  { id: 'bse500', label: 'BSE 500', subtitle: 'Broad Market Index' },
  { id: 'nifty50', label: 'NIFTY 50', subtitle: 'Broad Market Index' },
  { id: 'niftymidsml', label: 'NIFTY MidSmallcap 400', subtitle: 'Mid & Small Cap Index' }
] as const;

export function emptyBenchmarkMap(): Record<string, Record<string, number>> {
  return Object.fromEntries(benchmarkDefinitions.map((benchmark) => [benchmark.id, {}]));
}

export function benchmarkPerformancePlaceholders() {
  return benchmarkDefinitions.map((benchmark) => ({
    ...benchmark,
    metrics: {
      r1d: null, r1w: null, r1m: null, r3m: null, si: null,
      latestNav: null, latestDate: null
    }
  }));
}

export const benchmarkAvailability = {
  available: false,
  reason: 'TIGZIG does not provide complete benchmark index history'
} as const;

export const terAvailability = {
  available: false,
  reason: 'Factsheet expense-ratio data is not integrated'
} as const;
