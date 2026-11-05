import type pg from 'pg';
import { readSyncStatus } from '../repositories/sync-status.js';

export async function getSyncStatus(pool: pg.Pool, freshnessMaxMinutes: number) {
  const status = await readSyncStatus(pool);
  const generatedTimes = status.datasets
    .map((dataset) => dataset.manifestGeneratedAt?.getTime())
    .filter((value): value is number => value !== undefined);
  const latest = generatedTimes.length ? Math.max(...generatedTimes) : null;
  const ageSeconds = latest === null ? null : Math.max(0, Math.floor((Date.now() - latest) / 1000));
  return {
    ...status,
    sourceFreshness: {
      status: ageSeconds === null ? 'unknown' : ageSeconds <= freshnessMaxMinutes * 60 ? 'fresh' : 'stale',
      manifestGeneratedAt: latest === null ? null : new Date(latest),
      ageSeconds,
      thresholdMinutes: freshnessMaxMinutes
    }
  };
}
