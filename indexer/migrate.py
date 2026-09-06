"""One-shot CLI to migrate providers.json and evidence.json into the SQLite database."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_database,
    upsert_provider,
    insert_evidence,
    get_all_providers,
    DB_PATH,
)

INDEXER_DIR = Path(__file__).parent


def migrate_providers() -> tuple[int, int]:
    """Migrate providers.json to SQLite.
    Returns (total_in_file, migrated)."""
    providers_path = INDEXER_DIR / "providers.json"
    if not providers_path.exists():
        print("providers.json not found, skipping providers migration.")
        return 0, 0

    with open(providers_path, "r", encoding="utf-8") as f:
        providers = json.load(f)

    migrated = 0
    for provider in providers:
        try:
            upsert_provider(provider)
            migrated += 1
        except Exception as exc:
            print(f"  Failed to migrate provider {provider.get('provider_id')}: {exc}")

    print(f"Providers: {migrated}/{len(providers)} migrated")
    return len(providers), migrated


def migrate_evidence() -> tuple[int, int]:
    """Migrate evidence.json to SQLite.
    Returns (total_in_file, migrated)."""
    evidence_path = INDEXER_DIR / "evidence.json"
    if not evidence_path.exists():
        print("evidence.json not found, skipping evidence migration.")
        return 0, 0

    with open(evidence_path, "r", encoding="utf-8") as f:
        evidence_list = json.load(f)

    migrated = 0
    for record in evidence_list:
        try:
            insert_evidence(record)
            migrated += 1
        except Exception as exc:
            key = record.get("evidenceKey", {})
            print(f"  Failed to migrate evidence {key.get('transactionHash', 'unknown')}: {exc}")

    print(f"Evidence: {migrated}/{len(evidence_list)} migrated")
    return len(evidence_list), migrated


def verify_migration() -> None:
    """Verify that data in SQLite matches JSON files."""
    db_providers = get_all_providers()
    print(f"Database providers count: {len(db_providers)}")

    providers_path = INDEXER_DIR / "providers.json"
    if providers_path.exists():
        with open(providers_path, "r", encoding="utf-8") as f:
            json_providers = json.load(f)
        print(f"JSON providers count: {len(json_providers)}")
        if len(db_providers) == len(json_providers):
            print("Provider counts match.")
        else:
            print("WARNING: Provider counts do not match!")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Migrate JSON data to SQLite")
    parser.add_argument("--providers", action="store_true", help="Migrate only providers")
    parser.add_argument("--evidence", action="store_true", help="Migrate only evidence")
    parser.add_argument("--reset", action="store_true", help="Delete DB and re-migrate")
    parser.add_argument("--verify", action="store_true", help="Verify migration")
    args = parser.parse_args()

    if args.reset and DB_PATH.exists():
        DB_PATH.unlink()
        print("Deleted existing database.")

    if args.verify:
        verify_migration()
        return

    init_database()

    if args.providers:
        migrate_providers()
    elif args.evidence:
        migrate_evidence()
    else:
        print("Migrating providers...")
        migrate_providers()
        print("\nMigrating evidence...")
        migrate_evidence()

    print("\nMigration complete.")


if __name__ == "__main__":
    main()
