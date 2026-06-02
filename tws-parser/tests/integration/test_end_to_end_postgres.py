"""
End-to-end Postgres integration (plan §6 + §12.2).
Requires POSTGRES_* env vars + alembic migrations applied (`make migrate`).
"""
import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture
def pg_conn(postgres_env):
    import psycopg
    dsn = (f"host={postgres_env['POSTGRES_HOST']} port={postgres_env['POSTGRES_PORT']} "
           f"dbname={postgres_env['POSTGRES_DB']} user={postgres_env['POSTGRES_USER']} "
           f"password={postgres_env['POSTGRES_PASSWORD']}")
    conn = psycopg.connect(dsn)
    # Truncate before each test
    with conn.cursor() as cur:
        cur.execute("TRUNCATE tws.schedules, tws.jobs, tws.job_dependencies, "
                    "tws.schedule_dependencies, tws.resources, tws.job_resources, "
                    "tws.file_watchers, tws.job_file_dependencies RESTART IDENTITY CASCADE;")
    conn.commit()
    yield conn
    conn.close()


def _scalar(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def test_schedule_row_written(pg_conn, fixture_path):
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.rdbms.writer import RDBMSWriter

    schedules = parse_composer_text(str(fixture_path("01_single_schedule_single_job.txt")))
    RDBMSWriter(pg_conn).write_schedules(schedules)

    assert _scalar(pg_conn, "SELECT COUNT(*) FROM tws.schedules") == 1
    assert _scalar(pg_conn, "SELECT COUNT(*) FROM tws.jobs") == 1


def test_runtime_window_query_returns_expected_jobs(pg_conn, fixture_path):
    """Plan §12.2: the driving 5:30–6:30 query from the meeting."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.rdbms.writer import RDBMSWriter

    schedules = parse_composer_text(str(fixture_path("06_realistic_dump_many_schedules.txt")))
    RDBMSWriter(pg_conn).write_schedules(schedules)

    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT job_name, schedule_name, script_path
            FROM tws.v_runtime_window
            WHERE start_time >= '05:30' AND start_time < '06:30'
              AND script_path LIKE '%load_orders.bteq'
        """)
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "LOAD_ORDERS_TO_DW"
    assert rows[0][1] == "DAILY_SALES_LOAD"


def test_job_dependencies_table_populated(pg_conn, fixture_path):
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.rdbms.writer import RDBMSWriter

    RDBMSWriter(pg_conn).write_schedules(
        parse_composer_text(str(fixture_path("02_multi_job_with_follows.txt")))
    )

    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT j1.name, j2.name
            FROM tws.job_dependencies jd
            JOIN tws.jobs j1 ON j1.job_id = jd.job_id
            JOIN tws.jobs j2 ON j2.job_id = jd.depends_on_job_id
        """)
        pairs = set(cur.fetchall())
    assert ("TRANSFORM_ORDERS", "EXTRACT_ORDERS") in pairs
    assert ("LOAD_ORDERS_TO_DW", "TRANSFORM_ORDERS") in pairs


def test_file_watcher_query(pg_conn, fixture_path):
    """Plan §12.2: find schedules with file dependency on a feed."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.rdbms.writer import RDBMSWriter

    RDBMSWriter(pg_conn).write_schedules(
        parse_composer_text(str(fixture_path("06_realistic_dump_many_schedules.txt")))
    )

    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT s.name, j.name, fw.path
            FROM tws.schedules s
            JOIN tws.jobs j ON j.schedule_id = s.schedule_id
            JOIN tws.job_file_dependencies jfd ON jfd.job_id = j.job_id
            JOIN tws.file_watchers fw ON fw.file_watcher_id = jfd.file_watcher_id
            WHERE fw.path LIKE '/data/feeds/%'
        """)
        rows = cur.fetchall()
    assert any(r[1] == "WAIT_SALES_FEED" for r in rows)


def test_upsert_idempotent(pg_conn, fixture_path):
    """Plan §6 + §16: re-running uses ON CONFLICT DO UPDATE — no duplicate rows."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.rdbms.writer import RDBMSWriter

    path = str(fixture_path("02_multi_job_with_follows.txt"))
    schedules = parse_composer_text(path)
    RDBMSWriter(pg_conn).write_schedules(schedules)
    s_before = _scalar(pg_conn, "SELECT COUNT(*) FROM tws.schedules")
    j_before = _scalar(pg_conn, "SELECT COUNT(*) FROM tws.jobs")
    RDBMSWriter(pg_conn).write_schedules(schedules)
    assert _scalar(pg_conn, "SELECT COUNT(*) FROM tws.schedules") == s_before
    assert _scalar(pg_conn, "SELECT COUNT(*) FROM tws.jobs") == j_before
