export const schedulerJobs = ['nav', 'factsheets'] as const;
export type SchedulerJobId = typeof schedulerJobs[number];
const istOffset = 330 * 60000;

// Read the host's UTC wall clock, then apply the requested IST calendar schedule.
// No dependency on the phone timezone, Windows, process TZ, or a fixed UTC date.
export function scheduledSlot(job: SchedulerJobId, now: Date, next = false): Date {
  const ist = new Date(now.getTime() + istOffset);
  const day = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate()) - istOffset;
  if (job === 'factsheets') {
    const slot = day + ((11 * 60 + 1) * 60 + 59) * 1000;
    return new Date(next ? slot > now.getTime() ? slot : slot + 86400000
      : slot <= now.getTime() ? slot : slot - 86400000);
  }
  const interval = 2 * 3600000;
  const slot = day + Math.floor((now.getTime() - day) / interval) * interval;
  return new Date(next ? slot + interval : slot);
}

export function retryTime(attempts: number, now: Date): Date | null {
  return attempts < 3 ? new Date(now.getTime() + 5 * 60000 * 2 ** (attempts - 1)) : null;
}

export interface DueJob {
  state: string; scheduled_for: Date | null; retry_at: Date | null; attempts: number;
}
export function dueAttempt(job: SchedulerJobId, row: DueJob, now: Date): { slot: Date; attempt: number } | null {
  const slot = scheduledSlot(job, now);
  if (!row.scheduled_for || row.scheduled_for < slot) return { slot, attempt: 1 };
  if (row.scheduled_for > slot || row.state === 'succeeded' || row.attempts >= 3) return null;
  if (row.retry_at && row.retry_at > now) return null;
  return { slot: row.scheduled_for, attempt: row.attempts + 1 };
}
