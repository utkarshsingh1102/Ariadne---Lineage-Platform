"""Postgres writer — uses psycopg directly so the test suite can pass a raw connection."""

from __future__ import annotations

from typing import Any

import psycopg

from tws_parser.config import settings
from tws_parser.models.domain import ScheduleIR
from tws_parser.parser.dependencies import resolve
from tws_parser.utils.ids import file_watcher_id, resource_id


class RDBMSWriter:
    def __init__(self, connection: psycopg.Connection | None = None, schema: str | None = None):
        """Pass either an existing `psycopg.Connection` or let the writer build
        one from the configured `POSTGRES_*` env vars."""
        self._owned = False
        if connection is None:
            connection = psycopg.connect(_dsn())
            self._owned = True
        self.conn = connection
        self.schema = schema or settings.postgres_schema

    def close(self) -> None:
        if self._owned and self.conn is not None:
            self.conn.close()
            self.conn = None

    def write_schedules(self, schedules: list[ScheduleIR]) -> dict[str, int]:
        with self.conn.cursor() as cur:
            self._upsert_schedules(cur, schedules)
            self._upsert_jobs(cur, schedules)
            self._upsert_resources_and_links(cur, schedules)
            self._upsert_filewatchers_and_links(cur, schedules)
            self._upsert_dependencies(cur, schedules)
        self.conn.commit()
        stats = {
            "schedules": len(schedules),
            "jobs": sum(len(s.jobs) for s in schedules),
        }
        return {"rows_written": sum(stats.values())}

    # ----- schedules ---------------------------------------------------------

    def _upsert_schedules(self, cur, schedules: list[ScheduleIR]) -> None:
        sql = f"""
            INSERT INTO {self.schema}.schedules
                (schedule_id, workstation, scheduler, name, run_cycle,
                 cron_equivalent, valid_from, valid_to, start_time, end_time,
                 priority, carry_forward, raw_definition, source_file)
            VALUES
                (%(schedule_id)s, %(workstation)s, %(scheduler)s, %(name)s, %(run_cycle)s,
                 %(cron_equivalent)s, %(valid_from)s, %(valid_to)s, %(start_time)s, %(end_time)s,
                 %(priority)s, %(carry_forward)s, %(raw_definition)s, %(source_file)s)
            ON CONFLICT (schedule_id) DO UPDATE SET
                workstation = EXCLUDED.workstation,
                scheduler = EXCLUDED.scheduler,
                name = EXCLUDED.name,
                run_cycle = EXCLUDED.run_cycle,
                cron_equivalent = EXCLUDED.cron_equivalent,
                valid_from = EXCLUDED.valid_from,
                valid_to = EXCLUDED.valid_to,
                start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time,
                priority = EXCLUDED.priority,
                carry_forward = EXCLUDED.carry_forward,
                raw_definition = EXCLUDED.raw_definition,
                source_file = EXCLUDED.source_file,
                parsed_at = NOW()
        """
        for s in schedules:
            cur.execute(sql, {
                "schedule_id": s.id,
                "workstation": s.workstation,
                "scheduler": s.scheduler or None,
                "name": s.name,
                "run_cycle": s.run_cycle,
                "cron_equivalent": s.cron_equivalent,
                "valid_from": _parse_date(s.valid_from),
                "valid_to": _parse_date(s.valid_to),
                "start_time": _parse_time(s.start_time),
                "end_time": _parse_time(s.end_time),
                "priority": s.priority,
                "carry_forward": s.carry_forward,
                "raw_definition": s.raw_definition or None,
                "source_file": s.source_file or None,
            })

    # ----- jobs --------------------------------------------------------------

    def _upsert_jobs(self, cur, schedules: list[ScheduleIR]) -> None:
        sql = f"""
            INSERT INTO {self.schema}.jobs
                (job_id, schedule_id, name, script_path, script_args, script_type,
                 stream_logon, recovery, description, priority, order_in_schedule)
            VALUES
                (%(job_id)s, %(schedule_id)s, %(name)s, %(script_path)s, %(script_args)s,
                 %(script_type)s, %(stream_logon)s, %(recovery)s, %(description)s,
                 %(priority)s, %(order_in_schedule)s)
            ON CONFLICT (job_id) DO UPDATE SET
                schedule_id = EXCLUDED.schedule_id,
                name = EXCLUDED.name,
                script_path = EXCLUDED.script_path,
                script_args = EXCLUDED.script_args,
                script_type = EXCLUDED.script_type,
                stream_logon = EXCLUDED.stream_logon,
                recovery = EXCLUDED.recovery,
                description = EXCLUDED.description,
                priority = EXCLUDED.priority,
                order_in_schedule = EXCLUDED.order_in_schedule,
                parsed_at = NOW()
        """
        for s in schedules:
            for j in s.jobs:
                cur.execute(sql, {
                    "job_id": j.id, "schedule_id": j.schedule_id, "name": j.name,
                    "script_path": j.script_path, "script_args": j.script_args,
                    "script_type": j.script_type, "stream_logon": j.stream_logon,
                    "recovery": j.recovery, "description": j.description,
                    "priority": j.priority,
                    "order_in_schedule": j.order_in_schedule,
                })

    # ----- resources / file watchers -----------------------------------------

    def _upsert_resources_and_links(self, cur, schedules: list[ScheduleIR]) -> None:
        upsert_res = f"""
            INSERT INTO {self.schema}.resources (resource_id, name, quantity)
            VALUES (%(resource_id)s, %(name)s, %(quantity)s)
            ON CONFLICT (resource_id) DO UPDATE SET
                quantity = COALESCE(EXCLUDED.quantity, {self.schema}.resources.quantity)
        """
        upsert_link = f"""
            INSERT INTO {self.schema}.job_resources (job_id, resource_id, quantity_needed)
            VALUES (%(job_id)s, %(resource_id)s, %(quantity_needed)s)
            ON CONFLICT (job_id, resource_id) DO UPDATE SET
                quantity_needed = EXCLUDED.quantity_needed
        """
        seen_res: set[str] = set()
        for s in schedules:
            for j in s.jobs:
                for name, qty in j.needs:
                    rid = resource_id(name)
                    if rid not in seen_res:
                        cur.execute(upsert_res, {"resource_id": rid, "name": name,
                                                 "quantity": qty})
                        seen_res.add(rid)
                    cur.execute(upsert_link, {"job_id": j.id, "resource_id": rid,
                                              "quantity_needed": qty})

    def _upsert_filewatchers_and_links(self, cur, schedules: list[ScheduleIR]) -> None:
        upsert_fw = f"""
            INSERT INTO {self.schema}.file_watchers (file_watcher_id, path, pattern)
            VALUES (%(file_watcher_id)s, %(path)s, %(pattern)s)
            ON CONFLICT (file_watcher_id) DO UPDATE SET path = EXCLUDED.path
        """
        upsert_link = f"""
            INSERT INTO {self.schema}.job_file_dependencies (job_id, file_watcher_id)
            VALUES (%(job_id)s, %(file_watcher_id)s)
            ON CONFLICT DO NOTHING
        """
        seen_fw: set[str] = set()
        for s in schedules:
            for j in s.jobs:
                for path in j.opens:
                    fid = file_watcher_id(path)
                    if fid not in seen_fw:
                        cur.execute(upsert_fw, {"file_watcher_id": fid, "path": path,
                                                "pattern": None})
                        seen_fw.add(fid)
                    cur.execute(upsert_link, {"job_id": j.id, "file_watcher_id": fid})

    # ----- dependency edges --------------------------------------------------

    def _upsert_dependencies(self, cur, schedules: list[ScheduleIR]) -> None:
        deps = resolve(schedules)

        job_sql = f"""
            INSERT INTO {self.schema}.job_dependencies (job_id, depends_on_job_id)
            VALUES (%(job_id)s, %(depends_on_job_id)s)
            ON CONFLICT DO NOTHING
        """
        jid_by_sched_and_name = {
            (s.name, j.name): j.id for s in schedules for j in s.jobs
        }
        for jd in deps.job_dependencies:
            f_id = jid_by_sched_and_name.get((jd.schedule, jd.job))
            t_id = jid_by_sched_and_name.get((jd.schedule, jd.depends_on))
            if f_id and t_id:
                cur.execute(job_sql, {"job_id": f_id, "depends_on_job_id": t_id})

        sched_sql = f"""
            INSERT INTO {self.schema}.schedule_dependencies
                (schedule_id, depends_on_schedule_id)
            VALUES (%(schedule_id)s, %(depends_on_schedule_id)s)
            ON CONFLICT DO NOTHING
        """
        sid_by_name = {s.name: s.id for s in schedules}
        for sd in deps.schedule_dependencies:
            f_id = sid_by_name.get(sd.schedule)
            t_id = sid_by_name.get(sd.depends_on_schedule)
            if f_id and t_id:
                cur.execute(sched_sql, {"schedule_id": f_id,
                                        "depends_on_schedule_id": t_id})


# ----- helpers ---------------------------------------------------------------

def _dsn() -> str:
    return (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"dbname={settings.postgres_db} user={settings.postgres_user} "
        f"password={settings.postgres_password}"
    )


def _parse_time(raw: str | None) -> Any:
    if not raw:
        return None
    from datetime import time
    s = raw.strip()
    if ":" in s:
        try:
            hh, mm = s.split(":")
            return time(int(hh), int(mm))
        except ValueError:
            return None
    if len(s) == 4 and s.isdigit():
        return time(int(s[:2]), int(s[2:]))
    return None


def _parse_date(raw: str | None) -> Any:
    if not raw:
        return None
    from datetime import date
    s = raw.strip()
    # XML: YYYY-MM-DD; composer: MM/DD/YYYY
    if "-" in s and len(s) == 10:
        try:
            y, m, d = s.split("-")
            return date(int(y), int(m), int(d))
        except ValueError:
            return None
    if "/" in s and len(s) == 10:
        try:
            mm, dd, yyyy = s.split("/")
            return date(int(yyyy), int(mm), int(dd))
        except ValueError:
            return None
    return None
