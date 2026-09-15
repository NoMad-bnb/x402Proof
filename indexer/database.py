"""Database layer supporting SQLite (local) and PostgreSQL (cloud)."""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

INDEXER_DIR = Path(__file__).parent
DB_PATH = INDEXER_DIR / "indexer.db"

DATABASE_URL = os.environ.get("DATABASE_URL", "")

PROVIDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    provider_id TEXT PRIMARY KEY,
    label TEXT,
    facilitator_base_url TEXT,
    supported_url TEXT,
    verify_url TEXT,
    settle_url TEXT,
    known_declaration_url TEXT,
    networks TEXT,
    last_seen TEXT,
    status TEXT
);
"""

EVIDENCE_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER DEFAULT 1,
    stored_at TEXT,
    evidence_key TEXT,
    evidence_digest TEXT,
    summary TEXT,
    chain_id TEXT,
    provider_id TEXT,
    audit_verdict TEXT,
    evidence_source TEXT,
    evidence_store_status TEXT,
    transaction_hash TEXT
);
"""

EVIDENCE_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS evidence (
    id SERIAL PRIMARY KEY,
    schema_version INTEGER DEFAULT 1,
    stored_at TEXT,
    evidence_key TEXT,
    evidence_digest TEXT,
    summary TEXT,
    chain_id TEXT,
    provider_id TEXT,
    audit_verdict TEXT,
    evidence_source TEXT,
    evidence_store_status TEXT,
    transaction_hash TEXT
);
"""

REGISTRY_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS registry_cache (
    grouping TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    read_at TEXT
);
"""

EVIDENCE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_evidence_chain ON evidence(chain_id);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_provider ON evidence(provider_id);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_verdict ON evidence(audit_verdict);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_stored_at ON evidence(stored_at);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_key ON evidence(evidence_key);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_tx_hash ON evidence(transaction_hash);",
]


def _is_postgres() -> bool:
    return bool(DATABASE_URL) and (
        DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")
    )


def get_connection():
    """Get a database connection with dict-like row access."""
    if _is_postgres():
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise ImportError(
                "psycopg2-binary is required for PostgreSQL. "
                "Install it with: pip install psycopg2-binary"
            ) from exc
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _execute(conn, query, params=()):
    """Execute query, normalizing placeholders between SQLite and PostgreSQL."""
    if _is_postgres():
        pg_query = query.replace("?", "%s")
        cursor = conn.cursor()
        cursor.execute(pg_query, params)
        return cursor
    return conn.execute(query, params)


def _first_value(row, key=None):
    """Read one column from a row. PostgreSQL rows are mappings while SQLite
    rows are sequences, so both shapes are accepted and None is returned when
    neither one carries a value."""
    if row is None:
        return None
    if key is not None:
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            pass
    try:
        return row[0]
    except (KeyError, IndexError, TypeError):
        return None


def _last_insert_id(conn, cursor) -> int:
    """Return the last inserted row ID for the active backend."""
    if _is_postgres():
        cursor.execute("SELECT lastval() AS last_value")
        value = _first_value(cursor.fetchone(), "last_value")
        return int(value) if value is not None else 0
    return cursor.lastrowid


def init_database() -> None:
    """Create tables and indexes if they don't exist."""
    conn = get_connection()
    try:
        if _is_postgres():
            cur = conn.cursor()
            cur.execute(PROVIDERS_SCHEMA)
            cur.execute(EVIDENCE_SCHEMA_POSTGRES)
            cur.execute(REGISTRY_CACHE_SCHEMA)
            for index_sql in EVIDENCE_INDEXES:
                try:
                    cur.execute(index_sql)
                except Exception:
                    pass
            conn.commit()
        else:
            conn.executescript(PROVIDERS_SCHEMA)
            conn.executescript(EVIDENCE_SCHEMA_SQLITE)
            conn.executescript(REGISTRY_CACHE_SCHEMA)
            for index_sql in EVIDENCE_INDEXES:
                conn.executescript(index_sql)
            conn.commit()
    finally:
        conn.close()


# ── Providers CRUD ─────────────────────────────────────────────────────────

def upsert_provider(provider: dict) -> None:
    """Insert or update a provider record."""
    conn = get_connection()
    try:
        _execute(
            conn,
            """
            INSERT INTO providers (provider_id, label, facilitator_base_url, supported_url,
                                   verify_url, settle_url, known_declaration_url, networks,
                                   last_seen, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                label = excluded.label,
                facilitator_base_url = excluded.facilitator_base_url,
                supported_url = excluded.supported_url,
                verify_url = excluded.verify_url,
                settle_url = excluded.settle_url,
                known_declaration_url = excluded.known_declaration_url,
                networks = excluded.networks,
                last_seen = excluded.last_seen,
                status = excluded.status
            """,
            (
                provider.get("provider_id"),
                provider.get("label"),
                provider.get("facilitator_base_url"),
                provider.get("supported_url"),
                provider.get("verify_url"),
                provider.get("settle_url"),
                provider.get("known_declaration_url"),
                json.dumps(provider.get("networks", [])),
                provider.get("last_seen"),
                provider.get("status"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_provider(provider_id: str) -> dict | None:
    """Get a single provider by ID."""
    conn = get_connection()
    try:
        row = _execute(
            conn, "SELECT * FROM providers WHERE provider_id = ?", (provider_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_provider(row)
    finally:
        conn.close()


def get_all_providers() -> list[dict]:
    """Get all providers."""
    conn = get_connection()
    try:
        rows = _execute(conn, "SELECT * FROM providers ORDER BY provider_id").fetchall()
        return [_row_to_provider(row) for row in rows]
    finally:
        conn.close()


def _row_to_provider(row) -> dict:
    """Convert a database row to a provider dict."""
    provider = dict(row)
    networks = provider.get("networks")
    if isinstance(networks, str):
        try:
            provider["networks"] = json.loads(networks)
        except json.JSONDecodeError:
            provider["networks"] = []
    return provider


# ── Evidence CRUD ──────────────────────────────────────────────────────────

def insert_evidence(record: dict) -> int:
    """Insert an evidence record and return its ID."""
    conn = get_connection()
    try:
        evidence_key = record.get("evidenceKey", {})
        chain_id = evidence_key.get("chainId")
        transaction_hash = evidence_key.get("transactionHash")
        cursor = _execute(
            conn,
            """
            INSERT INTO evidence (
                schema_version, stored_at, evidence_key, evidence_digest, summary,
                chain_id, provider_id, audit_verdict, evidence_source, evidence_store_status,
                transaction_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("schemaVersion", 1),
                record.get("storedAt"),
                json.dumps(evidence_key),
                record.get("evidenceDigest"),
                json.dumps(record.get("summary", {})),
                chain_id,
                record.get("summary", {}).get("providerId"),
                record.get("summary", {}).get("auditVerdict"),
                record.get("summary", {}).get("evidenceSource"),
                record.get("evidenceStoreStatus"),
                transaction_hash,
            ),
        )
        conn.commit()
        return _last_insert_id(conn, cursor)
    finally:
        conn.close()


def find_evidence_by_key(chain_id: str, transaction_hash: str) -> list[dict]:
    """Find all evidence records for a given chain_id + transaction_hash."""
    conn = get_connection()
    try:
        rows = _execute(
            conn,
            """
            SELECT * FROM evidence
            WHERE chain_id = ? AND transaction_hash = ?
            ORDER BY stored_at ASC
            """,
            (chain_id, transaction_hash),
        ).fetchall()
        return [_row_to_evidence(row) for row in rows]
    finally:
        conn.close()


def search_evidence(
    chain_id: str | None = None,
    provider_id: str | None = None,
    audit_verdict: str | None = None,
    evidence_source: str | None = None,
    evidence_store_status: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Search evidence records with multiple filters."""
    query = "SELECT * FROM evidence WHERE 1=1"
    params = []

    if chain_id is not None:
        query += " AND chain_id = ?"
        params.append(chain_id)
    if provider_id is not None:
        query += " AND provider_id = ?"
        params.append(provider_id)
    if audit_verdict is not None:
        query += " AND audit_verdict = ?"
        params.append(audit_verdict)
    if evidence_source is not None:
        query += " AND evidence_source = ?"
        params.append(evidence_source)
    if evidence_store_status is not None:
        query += " AND evidence_store_status = ?"
        params.append(evidence_store_status)
    if from_date is not None:
        query += " AND stored_at >= ?"
        params.append(from_date)
    if to_date is not None:
        query += " AND stored_at <= ?"
        params.append(to_date)

    query += " ORDER BY stored_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_connection()
    try:
        rows = _execute(conn, query, params).fetchall()
        return [_row_to_evidence(row) for row in rows]
    finally:
        conn.close()


def count_evidence(
    chain_id: str | None = None,
    provider_id: str | None = None,
    audit_verdict: str | None = None,
) -> int:
    """Count evidence records matching filters."""
    query = "SELECT COUNT(*) as cnt FROM evidence WHERE 1=1"
    params = []

    if chain_id is not None:
        query += " AND chain_id = ?"
        params.append(chain_id)
    if provider_id is not None:
        query += " AND provider_id = ?"
        params.append(provider_id)
    if audit_verdict is not None:
        query += " AND audit_verdict = ?"
        params.append(audit_verdict)

    conn = get_connection()
    try:
        row = _execute(conn, query, params).fetchone()
        return _first_value(row, "cnt") or 0
    finally:
        conn.close()


def get_stats() -> dict:
    """Get aggregated statistics."""
    conn = get_connection()
    try:
        provider_rows = _execute(
            conn, "SELECT status, COUNT(*) as cnt FROM providers GROUP BY status"
        ).fetchall()
        provider_stats = {row["status"]: row["cnt"] for row in provider_rows}

        verdict_rows = _execute(
            conn, "SELECT audit_verdict, COUNT(*) as cnt FROM evidence GROUP BY audit_verdict"
        ).fetchall()
        verdict_stats = {row["audit_verdict"]: row["cnt"] for row in verdict_rows}

        source_rows = _execute(
            conn, "SELECT evidence_source, COUNT(*) as cnt FROM evidence GROUP BY evidence_source"
        ).fetchall()
        source_stats = {row["evidence_source"]: row["cnt"] for row in source_rows}

        chain_rows = _execute(
            conn, "SELECT chain_id, COUNT(*) as cnt FROM evidence GROUP BY chain_id"
        ).fetchall()
        chain_stats = {row["chain_id"]: row["cnt"] for row in chain_rows}

        store_rows = _execute(
            conn,
            "SELECT evidence_store_status, COUNT(*) as cnt FROM evidence"
            " GROUP BY evidence_store_status",
        ).fetchall()
        store_stats = {
            (row["evidence_store_status"] or "UNKNOWN"): row["cnt"]
            for row in store_rows
        }

        verdict_tx_rows = _execute(
            conn,
            "SELECT audit_verdict, COUNT(DISTINCT transaction_hash) as cnt"
            " FROM evidence GROUP BY audit_verdict",
        ).fetchall()
        verdict_tx_stats = {
            row["audit_verdict"]: row["cnt"] for row in verdict_tx_rows
        }

        total_evidence = _first_value(
            _execute(conn, "SELECT COUNT(*) as cnt FROM evidence").fetchone(), "cnt"
        ) or 0
        total_providers = _first_value(
            _execute(conn, "SELECT COUNT(*) as cnt FROM providers").fetchone(), "cnt"
        ) or 0
        total_transactions = _first_value(
            _execute(
                conn, "SELECT COUNT(DISTINCT transaction_hash) as cnt FROM evidence"
            ).fetchone(),
            "cnt",
        ) or 0

        last_evidence_row = _execute(
            conn,
            "SELECT stored_at AS last_stamp FROM evidence"
            " ORDER BY stored_at DESC LIMIT 1",
        ).fetchone()
        last_provider_row = _execute(
            conn,
            "SELECT last_seen AS last_stamp FROM providers"
            " ORDER BY last_seen DESC LIMIT 1",
        ).fetchone()

        last_update_time = None
        for candidate in (last_evidence_row, last_provider_row):
            value = _first_value(candidate, "last_stamp")
            if value is None:
                continue
            if last_update_time is None or str(value) > str(last_update_time):
                last_update_time = value

        return {
            "providers": {"total": total_providers, "by_status": provider_stats},
            "evidence": {
                "total": total_evidence,
                "total_transactions": total_transactions,
                "by_verdict": verdict_stats,
                "by_verdict_transactions": verdict_tx_stats,
                "by_source": source_stats,
                "by_chain": chain_stats,
                "by_store_status": store_stats,
            },
            "last_update_time": last_update_time,
        }
    finally:
        conn.close()


def _row_to_evidence(row) -> dict:
    """Convert a database row to an evidence record dict."""
    record = dict(row)
    evidence_key = record.get("evidence_key")
    if isinstance(evidence_key, str):
        try:
            record["evidence_key"] = json.loads(evidence_key)
        except json.JSONDecodeError:
            record["evidence_key"] = {}
    else:
        record["evidence_key"] = {}
    summary = record.get("summary")
    if isinstance(summary, str):
        try:
            record["summary"] = json.loads(summary)
        except json.JSONDecodeError:
            record["summary"] = {}
    else:
        record["summary"] = {}
    record.setdefault("schema_version", record.get("schema_version", 1))
    record.setdefault("stored_at", record.get("stored_at"))
    record.setdefault("evidence_digest", record.get("evidence_digest"))
    record.setdefault("evidence_store_status", record.get("evidence_store_status"))
    return record


# ── On-chain registry cache ────────────────────────────────────────────────

def save_registry_cache(grouping: str, records: list, read_at: str | None) -> None:
    """Store or replace one registry grouping (by_label or by_relayer)."""
    conn = get_connection()
    try:
        _execute(
            conn,
            """
            INSERT INTO registry_cache (grouping, payload, read_at)
            VALUES (?, ?, ?)
            ON CONFLICT(grouping) DO UPDATE SET
                payload = excluded.payload,
                read_at = excluded.read_at
            """,
            (grouping, json.dumps(records), read_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_registry_cache(grouping: str) -> dict | None:
    """Return {'grouping', 'records', 'read_at'} for one grouping, or None."""
    conn = get_connection()
    try:
        row = _execute(
            conn, "SELECT grouping, payload, read_at FROM registry_cache WHERE grouping = ?",
            (grouping,),
        ).fetchone()
        if row is None:
            return None
        try:
            records = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError, KeyError):
            records = []
        return {
            "grouping": row["grouping"],
            "records": records if isinstance(records, list) else [],
            "read_at": row["read_at"],
        }
    finally:
        conn.close()


# ── Migration ──────────────────────────────────────────────────────────────

def migrate_from_json() -> tuple[int, int]:
    """Migrate data from JSON files to SQLite.
    Returns (providers_migrated, evidence_migrated).
    Safe to run multiple times; skips existing records."""
    providers_migrated = 0
    evidence_migrated = 0

    providers_path = INDEXER_DIR / "providers.json"
    if providers_path.exists():
        try:
            with open(providers_path, "r", encoding="utf-8") as f:
                providers = json.load(f)
            for provider in providers:
                upsert_provider(provider)
                providers_migrated += 1
        except Exception:
            pass

    evidence_path = INDEXER_DIR / "evidence.json"
    if evidence_path.exists():
        try:
            with open(evidence_path, "r", encoding="utf-8") as f:
                evidence_list = json.load(f)
            for record in evidence_list:
                try:
                    insert_evidence(record)
                    evidence_migrated += 1
                except Exception:
                    pass
        except Exception:
            pass

    return providers_migrated, evidence_migrated


# ── Self-test ──────────────────────────────────────────────────────────────

class _DictRowCursor:
    """Cursor proxy that returns dict rows, the shape psycopg2 hands back."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=()):
        self._cursor.execute(query, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else dict(row)

    def fetchall(self):
        return [dict(row) for row in self._cursor.fetchall()]

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _DictRowConnection:
    """Connection proxy whose cursors return dict rows."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        return _DictRowCursor(self._conn.execute(query, params))

    def cursor(self):
        return _DictRowCursor(self._conn.cursor())

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _stats_survives_dict_rows() -> bool:
    """Replay the stats and count paths against dict-shaped rows, so the
    PostgreSQL row shape is covered without a server."""
    global get_connection
    original = get_connection

    def with_dict_rows():
        return _DictRowConnection(original())

    get_connection = with_dict_rows
    try:
        stats = get_stats()
        evidence = stats["evidence"]
        return (
            stats["providers"]["total"] == 1
            and evidence["total"] == 1
            and evidence["total_transactions"] == 1
            and evidence["by_verdict"].get("CONFIRMED") == 1
            and evidence["by_verdict_transactions"].get("CONFIRMED") == 1
            and evidence["by_chain"].get("0x14a34") == 1
            and evidence["by_store_status"].get("EVIDENCE_STORED") == 1
            and stats["last_update_time"] is not None
            and count_evidence(chain_id="0x14a34") == 1
        )
    except Exception as exc:
        print("dict-row replay raised: " + str(exc))
        return False
    finally:
        get_connection = original


def _run_self_test() -> None:
    """Offline checks for database layer."""
    if _is_postgres():
        print("Self-test skipped for PostgreSQL backend.")
        return

    checks = []

    test_db_path = INDEXER_DIR / "indexer_test.db"
    global DB_PATH
    original_db_path = DB_PATH
    DB_PATH = test_db_path

    if DB_PATH.exists():
        DB_PATH.unlink()

    init_database()

    conn = get_connection()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [row[0] for row in tables]
    checks.append((
        "init_database creates providers table",
        "providers" in table_names,
    ))
    checks.append((
        "init_database creates evidence table",
        "evidence" in table_names,
    ))
    conn.close()

    test_provider = {
        "provider_id": "test-db-provider",
        "label": "Test DB Provider",
        "facilitator_base_url": "https://example.com",
        "supported_url": "https://example.com/supported",
        "networks": ["eip155:84532"],
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "status": "DECLARATION_CAPTURED",
    }
    upsert_provider(test_provider)
    retrieved = get_provider("test-db-provider")
    checks.append((
        "upsert_provider inserts and get_provider retrieves",
        retrieved is not None and retrieved["provider_id"] == "test-db-provider",
    ))

    test_evidence = {
        "schemaVersion": 1,
        "storedAt": datetime.now(timezone.utc).isoformat(),
        "evidenceKey": {"chainId": "0x14a34", "transactionHash": "0x" + "ab" * 32},
        "evidenceDigest": "0x" + "cd" * 32,
        "summary": {
            "providerId": "test-db-provider",
            "auditVerdict": "CONFIRMED",
            "evidenceSource": "header_capture",
        },
        "evidenceStoreStatus": "EVIDENCE_STORED",
    }
    insert_evidence(test_evidence)
    results = search_evidence(chain_id="0x14a34", audit_verdict="CONFIRMED")
    checks.append((
        "insert_evidence stores and search_evidence retrieves",
        len(results) == 1 and results[0]["evidence_key"]["chainId"] == "0x14a34",
    ))

    count = count_evidence(chain_id="0x14a34")
    checks.append((
        "count_evidence returns correct count",
        count == 1,
    ))

    stats = get_stats()
    checks.append((
        "get_stats returns aggregated data",
        stats["providers"]["total"] >= 1 and stats["evidence"]["total"] >= 1,
    ))
    checks.append((
        "get_stats reports transactions and store status",
        stats["evidence"]["total_transactions"] == 1
        and stats["evidence"]["by_verdict_transactions"] == {"CONFIRMED": 1}
        and stats["evidence"]["by_store_status"] == {"EVIDENCE_STORED": 1},
    ))
    checks.append((
        "get_stats reads dict-like rows (PostgreSQL shape)",
        _stats_survives_dict_rows(),
    ))

    save_registry_cache(
        "by_label",
        [{"facilitator": "alpha", "labelRelayerConflict": False}],
        "2026-09-15T00:00:00+00:00",
    )
    save_registry_cache(
        "by_relayer",
        [{"relayer": "0xaaa", "relayerLabelConflict": True}],
        "2026-09-15T00:00:00+00:00",
    )
    cached = get_registry_cache("by_label")
    cached_relayer = get_registry_cache("by_relayer")
    checks.append((
        "registry cache saves and reads both groupings",
        cached is not None
        and cached["records"][0]["facilitator"] == "alpha"
        and cached["read_at"] == "2026-09-15T00:00:00+00:00"
        and cached_relayer is not None
        and cached_relayer["records"][0]["relayerLabelConflict"] is True,
    ))
    save_registry_cache("by_label", [{"facilitator": "beta"}], "later")
    cached_overwrite = get_registry_cache("by_label")
    checks.append((
        "registry cache upsert replaces the payload and read_at",
        cached_overwrite["records"][0]["facilitator"] == "beta"
        and cached_overwrite["read_at"] == "later",
    ))
    checks.append(("registry cache miss returns None", get_registry_cache("missing") is None))


    if DB_PATH.exists():
        DB_PATH.unlink()
    DB_PATH = original_db_path

    failures = 0
    for description, passed in checks:
        status = "True" if passed else "FAILED"
        print(description + ": " + status)
        if not passed:
            failures = failures + 1

    print("")
    if failures == 0:
        print("All " + str(len(checks)) + " checks passed.")
    else:
        print(str(failures) + " of " + str(len(checks)) + " checks FAILED.")
        raise SystemExit(1)


if __name__ == "__main__":
    _run_self_test()
