"""Environment-only configuration shared by migrations and the TIGZIG worker."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_environment():
    """Use the same selected environment for database, downloads, and schedules."""
    load_dotenv(PROJECT_ROOT / os.environ.get('SIF_ENV_FILE', '.env'), override=False)


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)
    sslmode: str
    connect_timeout: int
    statement_timeout_ms: int

    @classmethod
    def from_env(cls):
        load_environment()
        required = ('PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD')
        for name in required:
            if not os.environ.get(name, '').strip():
                raise ValueError(f'Missing environment variable: {name}')
        expected = os.environ.get('PGEXPECTED_DATABASE', 'VB-SIF').strip() or 'VB-SIF'
        if os.environ['PGDATABASE'] != expected:
            raise ValueError(f'PGDATABASE must be {expected} (PGEXPECTED_DATABASE)')
        sslmode = os.environ.get('PGSSLMODE', 'disable')
        if sslmode not in ('disable', 'verify-full'):
            raise ValueError('PGSSLMODE must be disable or verify-full')
        if os.environ.get('APP_TIMEZONE', 'Asia/Kolkata') != 'Asia/Kolkata':
            raise ValueError('APP_TIMEZONE must be Asia/Kolkata')
        def integer(name, fallback, maximum):
            raw = os.environ.get(name, str(fallback))
            if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= maximum:
                raise ValueError(f'Invalid environment variable: {name}')
            return int(raw)
        return cls(
            host=os.environ['PGHOST'], port=integer('PGPORT', 5432, 65535),
            dbname=os.environ['PGDATABASE'], user=os.environ['PGUSER'],
            password=os.environ['PGPASSWORD'], sslmode=sslmode,
            connect_timeout=integer('PGCONNECT_TIMEOUT', 10, 300),
            statement_timeout_ms=integer('PGSTATEMENT_TIMEOUT_MS', 15000, 3600000),
        )

    def connection_kwargs(self):
        kwargs = {
            'host': self.host, 'port': self.port, 'dbname': self.dbname,
            'user': self.user, 'password': self.password, 'sslmode': self.sslmode,
            'connect_timeout': self.connect_timeout,
            'application_name': 'vb-sif-foundation',
            'options': f'-c search_path=public -c timezone=Asia/Kolkata -c statement_timeout={self.statement_timeout_ms}',
        }
        if os.environ.get('PGSSLROOTCERT'):
            kwargs['sslrootcert'] = str((PROJECT_ROOT / os.environ['PGSSLROOTCERT']).resolve())
        return kwargs


@dataclass(frozen=True)
class TigzigConfig:
    base_url: str
    download_dir: Path
    timeout_seconds: int
    retries: int
    backoff_seconds: int

    @classmethod
    def from_env(cls):
        load_environment()
        base_url = os.environ.get('TIGZIG_BASE_URL', 'https://sif.tigzig.com/')
        if not base_url.startswith('https://') or not base_url.endswith('/'):
            raise ValueError('TIGZIG_BASE_URL must be an HTTPS URL ending in /')

        raw_directory = Path(os.environ.get('TIGZIG_DOWNLOAD_DIR', 'data/tigzig'))
        download_dir = raw_directory if raw_directory.is_absolute() else PROJECT_ROOT / raw_directory

        def integer(name, fallback, minimum, maximum):
            raw = os.environ.get(name, str(fallback))
            if not raw.isascii() or not raw.isdecimal() or not minimum <= int(raw) <= maximum:
                raise ValueError(f'Invalid environment variable: {name}')
            return int(raw)

        return cls(
            base_url=base_url,
            download_dir=download_dir.resolve(),
            timeout_seconds=integer('TIGZIG_HTTP_TIMEOUT_SECONDS', 30, 1, 300),
            retries=integer('TIGZIG_HTTP_RETRIES', 3, 0, 10),
            backoff_seconds=integer('TIGZIG_HTTP_BACKOFF_SECONDS', 1, 0, 60),
        )


@dataclass(frozen=True)
class ScheduleSpec:
    job_id: str
    label: str
    hour: int
    minute: int
    trigger_type: str = 'scheduled'


@dataclass(frozen=True)
class SchedulerConfig:
    timezone: str
    schedules: tuple[ScheduleSpec, ...]
    max_retries: int
    backoff_seconds: int
    max_backoff_seconds: int
    jitter_seconds: int
    misfire_grace_seconds: int
    startup_recovery: bool

    @classmethod
    def from_env(cls):
        load_environment()
        timezone_name = os.environ.get('APP_TIMEZONE', 'Asia/Kolkata')
        if timezone_name != 'Asia/Kolkata':
            raise ValueError('APP_TIMEZONE must be Asia/Kolkata')

        def integer(name: str, fallback: int, minimum: int, maximum: int) -> int:
            raw = os.environ.get(name, str(fallback))
            if not raw.isascii() or not raw.isdecimal() or not minimum <= int(raw) <= maximum:
                raise ValueError(f'Invalid environment variable: {name}')
            return int(raw)

        def clock(name: str, fallback: str) -> tuple[int, int]:
            raw = os.environ.get(name, fallback)
            match = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)', raw)
            if match is None:
                raise ValueError(f'Invalid environment variable: {name}')
            return int(match.group(1)), int(match.group(2))

        def boolean(name: str, fallback: bool) -> bool:
            raw = os.environ.get(name, str(fallback).lower()).lower()
            if raw not in ('true', 'false'):
                raise ValueError(f'Invalid environment variable: {name}')
            return raw == 'true'

        definitions = (
            ('metadata_nav_early', 'Metadata + NAV', 'TIGZIG_SCHEDULE_EARLY', '05:20', 'scheduled'),
            ('metadata_nav_midday', 'Metadata + NAV', 'TIGZIG_SCHEDULE_MIDDAY', '12:00', 'scheduled'),
            ('aaum_check', 'AAUM check', 'TIGZIG_SCHEDULE_AAUM', '12:15', 'scheduled'),
            ('catch_up', 'Catch-up', 'TIGZIG_SCHEDULE_CATCHUP', '18:00', 'scheduled'),
            ('recovery', 'Recovery', 'TIGZIG_SCHEDULE_RECOVERY', '21:00', 'recovery'),
        )
        schedules = []
        for job_id, label, name, fallback, trigger_type in definitions:
            hour, minute = clock(name, fallback)
            schedules.append(ScheduleSpec(job_id, label, hour, minute, trigger_type))
        return cls(
            timezone=timezone_name,
            schedules=tuple(schedules),
            max_retries=integer('TIGZIG_JOB_MAX_RETRIES', 3, 0, 20),
            backoff_seconds=integer('TIGZIG_JOB_BACKOFF_SECONDS', 30, 0, 3600),
            max_backoff_seconds=integer('TIGZIG_JOB_MAX_BACKOFF_SECONDS', 900, 1, 86400),
            jitter_seconds=integer('TIGZIG_JOB_JITTER_SECONDS', 15, 0, 3600),
            misfire_grace_seconds=integer(
                'TIGZIG_JOB_MISFIRE_GRACE_SECONDS', 3600, 1, 86400
            ),
            startup_recovery=boolean('TIGZIG_RUN_STARTUP_RECOVERY', True),
        )
