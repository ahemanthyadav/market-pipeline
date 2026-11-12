"""psql-compatible query helper shared by the ML jobs (ml1.py, ml3.py).

The ML tools were written as workstation scripts that shell out to
`$PSQL_CMD -At -F, -c <sql>` and parse the text. In-cluster there is no psql
client, so when PSQL_CMD is unset and PGHOST is set this module runs the SQL
through psycopg and renders the result the same way psql `-At -F,` does:

  * row-returning statements → one line per row, fields joined by ","
    (NULL → "", booleans → "t"/"f", json/jsonb → compact JSON)
  * other statements → the command status ("INSERT 0 3"), which the callers
    parse to count rows written.

The connection is opened once per process, autocommit (each statement stands
alone, matching psql -c). Workstation behaviour is unchanged.
"""
import json
import os
import subprocess
import sys

_CONN = None


def _q(s):
    """Single-quote a string for the shell (workstation path)."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def fmt_value(v):
    """Render one value like psql's unaligned output."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(", ", ": "), default=str)
    return str(v)


def render(description, rows, statusmessage):
    """Render a cursor result as psql -At -F, text."""
    if description is None:
        return (statusmessage or "") + "\n"
    if not rows:
        return ""
    return "\n".join(",".join(fmt_value(v) for v in row) for row in rows) + "\n"


def _conn():
    global _CONN
    if _CONN is None or _CONN.closed:
        import psycopg  # only needed in-cluster
        _CONN = psycopg.connect(autocommit=True, connect_timeout=30)  # libpq PG* env
    return _CONN


def psql(sql, text=True):
    cmd = os.environ.get("PSQL_CMD")
    if cmd:
        out = subprocess.run(cmd + " -v ON_ERROR_STOP=1 -At -F, -c " + _q(sql), shell=True,
                             check=True, capture_output=True, text=text)
        return out.stdout
    if not os.environ.get("PGHOST"):
        sys.exit("set PSQL_CMD (workstation) or PGHOST/PGUSER/PGPASSWORD (in-cluster)")
    with _conn().cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() if cur.description is not None else None
        return render(cur.description, rows, cur.statusmessage)
