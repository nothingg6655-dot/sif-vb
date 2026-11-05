"""Configurable Asia/Kolkata scheduler for the lock-aware TIGZIG worker."""
from zoneinfo import ZoneInfo

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import SchedulerConfig
from .worker import log_event, run_sync_job


def build_scheduler(
    config: SchedulerConfig | None = None,
    runner=run_sync_job,
) -> BlockingScheduler:
    config = config or SchedulerConfig.from_env()
    timezone = ZoneInfo(config.timezone)
    scheduler = BlockingScheduler(
        timezone=timezone,
        executors={'default': ThreadPoolExecutor(max_workers=1)},
        job_defaults={
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': config.misfire_grace_seconds,
        },
    )
    for schedule in config.schedules:
        scheduler.add_job(
            runner,
            CronTrigger(
                hour=schedule.hour, minute=schedule.minute, timezone=timezone
            ),
            args=(schedule.job_id, schedule.trigger_type),
            id=schedule.job_id,
            name=schedule.label,
            replace_existing=True,
        )
    return scheduler


def run_scheduler(config: SchedulerConfig | None = None) -> None:
    config = config or SchedulerConfig.from_env()
    scheduler = build_scheduler(config)
    jobs = [
        {
            'job_identifier': job.id,
            'name': job.name,
            'trigger': str(job.trigger),
        }
        for job in scheduler.get_jobs()
    ]
    log_event('scheduler_configured', timezone=config.timezone, jobs=jobs)
    if config.startup_recovery:
        run_sync_job(
            'startup_recovery', 'recovery', recover_abandoned=True, config=config
        )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log_event('scheduler_stopped')


if __name__ == '__main__':
    run_scheduler()
