"""Milestone 5 scheduling, locking, retry, and restart recovery tests."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
import unittest
from threading import Event, Thread
from unittest.mock import patch
from zoneinfo import ZoneInfo
from apscheduler.events import EVENT_JOB_SUBMITTED
from apscheduler.triggers.date import DateTrigger

import psycopg

from database.migrate import verify_target
from ingestion.tigzig.config import DatabaseConfig, SchedulerConfig
from ingestion.tigzig.scheduler import build_scheduler
from ingestion.tigzig.worker import (
    ingestion_lock, log_event, mark_abandoned_runs, retry_delays,
)


class SchedulerTests(unittest.TestCase):
    def test_timer_dispatches_configured_job_with_its_identifier_and_trigger_type(self):
        config = SchedulerConfig.from_env()
        schedule = config.schedules[0]
        config = replace(config, schedules=(schedule,))
        fired = Event()
        submitted = Event()
        calls = []
        thread_errors = []

        def runner(*args):
            calls.append(args)
            fired.set()

        scheduler = build_scheduler(config, runner=runner)
        scheduler.add_listener(lambda _event: submitted.set(), EVENT_JOB_SUBMITTED)
        scheduler.reschedule_job(schedule.job_id, trigger=DateTrigger(
            run_date=datetime.now().astimezone() + timedelta(milliseconds=150),
        ))
        def start():
            try:
                scheduler.start()
            except BaseException as error:
                thread_errors.append(error)

        thread = Thread(target=start, daemon=True)
        thread.start()
        try:
            self.assertTrue(fired.wait(5), 'Scheduler did not execute the due job')
            # Submission events are published after the scheduler finishes updating
            # the job store. Wait before shutting down the in-memory scheduler.
            self.assertTrue(submitted.wait(5), 'Scheduler did not finish dispatching the job')
            self.assertEqual(calls, [(schedule.job_id, schedule.trigger_type)])
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=True)
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(thread_errors, [])

    def test_default_schedule_is_five_ist_jobs(self):
        names = [
            'TIGZIG_SCHEDULE_EARLY', 'TIGZIG_SCHEDULE_MIDDAY',
            'TIGZIG_SCHEDULE_AAUM', 'TIGZIG_SCHEDULE_CATCHUP',
            'TIGZIG_SCHEDULE_RECOVERY',
        ]
        with patch.dict(os.environ, {name: '' for name in names}, clear=False):
            for name in names:
                os.environ.pop(name, None)
            config = SchedulerConfig.from_env()
        self.assertEqual(config.timezone, 'Asia/Kolkata')
        self.assertEqual([(item.hour, item.minute) for item in config.schedules],
                         [(5, 20), (12, 0), (12, 15), (18, 0), (21, 0)])
        scheduler = build_scheduler(config, runner=lambda *_: None)
        self.assertEqual(len(scheduler.get_jobs()), 5)
        self.assertTrue(all(getattr(job.trigger.timezone, 'key', None) == 'Asia/Kolkata'
                            for job in scheduler.get_jobs()))

    def test_schedule_rejects_invalid_clock(self):
        with patch.dict(os.environ, {'TIGZIG_SCHEDULE_EARLY': '25:00'}):
            with self.assertRaisesRegex(ValueError, 'TIGZIG_SCHEDULE_EARLY'):
                SchedulerConfig.from_env()

    def test_trigger_times_are_ist_under_utc_singapore_and_us_clocks(self):
        config = SchedulerConfig.from_env()
        scheduler = build_scheduler(config, runner=lambda *_: None)
        for instant in [datetime(2026, 1, 5, 18, 30, tzinfo=timezone.utc),
                        datetime(2026, 7, 5, 18, 30, tzinfo=timezone.utc)]:
            ist_day = instant.astimezone(ZoneInfo('Asia/Kolkata')).date()
            for server_zone in ['UTC', 'Asia/Singapore', 'America/Los_Angeles']:
                server_now = instant.astimezone(ZoneInfo(server_zone))
                for spec in config.schedules:
                    job = next(job for job in scheduler.get_jobs() if job.id == spec.job_id)
                    due = job.trigger.get_next_fire_time(None, server_now)
                    expected = datetime.combine(ist_day, datetime.min.time()).replace(
                        hour=spec.hour, minute=spec.minute, tzinfo=ZoneInfo('Asia/Kolkata'))
                    self.assertEqual(due, expected, (server_zone, spec.job_id))
                    self.assertEqual(due.utcoffset(), timedelta(hours=5, minutes=30))

    def test_early_ist_job_rolls_over_to_the_correct_utc_day(self):
        config = replace(SchedulerConfig.from_env(), schedules=(
            replace(SchedulerConfig.from_env().schedules[0], hour=5, minute=20),))
        job = build_scheduler(config, runner=lambda *_: None).get_jobs()[0]
        before = datetime(2026, 11, 5, 23, 49, tzinfo=timezone.utc)
        first = job.trigger.get_next_fire_time(None, before)
        self.assertEqual(first.isoformat(), '2026-11-06T05:20:00+05:30')
        self.assertEqual(first.astimezone(timezone.utc).isoformat(), '2026-11-05T23:50:00+00:00')
        after = datetime(2026, 11, 5, 23, 51, tzinfo=timezone.utc)
        self.assertEqual(job.trigger.get_next_fire_time(None, after).isoformat(), '2026-11-07T05:20:00+05:30')

    def test_non_ist_scheduler_configuration_is_refused(self):
        config = replace(SchedulerConfig.from_env(), timezone='UTC')
        with self.assertRaisesRegex(ValueError, 'Asia/Kolkata'):
            build_scheduler(config, runner=lambda *_: None)
        with patch.dict(os.environ, {'APP_TIMEZONE': 'UTC'}):
            with self.assertRaisesRegex(ValueError, 'Asia/Kolkata'):
                SchedulerConfig.from_env()

    def test_environment_selection_is_shared_by_all_worker_configuration(self):
        from ingestion.tigzig.config import TigzigConfig
        with patch.dict(os.environ, {'SIF_ENV_FILE': '.env.local-backup'}), \
             patch('ingestion.tigzig.config.load_dotenv') as load:
            DatabaseConfig.from_env()
            TigzigConfig.from_env()
            SchedulerConfig.from_env()
        self.assertEqual(load.call_count, 3)
        self.assertTrue(all(call.args[0].name == '.env.local-backup' for call in load.call_args_list))

    def test_retry_delays_are_exponential_capped_and_jittered(self):
        base = SchedulerConfig.from_env()
        config = SchedulerConfig(base.timezone, base.schedules, 4, 10, 25, 5,
                                 base.misfire_grace_seconds, base.startup_recovery)
        self.assertEqual(retry_delays(config, jitter=lambda: 0.5),
                         [12.5, 22.5, 27.5, 27.5])

    def test_advisory_lock_excludes_second_database_session(self):
        with ingestion_lock() as (first, _):
            self.assertTrue(first)
            with ingestion_lock() as (second, _):
                self.assertFalse(second)

    def test_restart_recovery_interrupts_abandoned_runs(self):
        connection = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
        self.addCleanup(connection.close)
        self.addCleanup(connection.rollback)
        verify_target(connection)
        run_id = connection.execute("""
          INSERT INTO public.tigzig_sync_runs(trigger_type, job_type)
          VALUES ('test','sync') RETURNING run_id
        """).fetchone()[0]
        self.assertGreaterEqual(mark_abandoned_runs(connection), 1)
        status, message = connection.execute(
            'SELECT status,error_message FROM public.tigzig_sync_runs WHERE run_id=%s',
            (run_id,)).fetchone()
        self.assertEqual(status, 'interrupted')
        self.assertIn('recovered on restart', message)

    def test_structured_log_is_json(self):
        output = StringIO()
        with redirect_stdout(output):
            log_event('test_event', job_identifier='job-1', inserted=2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['event'], 'test_event')
        self.assertEqual(payload['job_identifier'], 'job-1')
        self.assertEqual(payload['inserted'], 2)
        self.assertIn('+05:30', payload['timestamp'])


if __name__ == '__main__':
    unittest.main()
