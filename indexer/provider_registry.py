"""Strict JSON CRUD registry of known x402 facilitators with full-schema validation."""

import json
import os
from datetime import datetime, timezone

REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "providers.json")

REQUIRED_FIELDS = [
    "provider_id",
    "label",
    "facilitator_base_url",
    "supported_url",
    "verify_url",
    "settle_url",
    "known_declaration_url",
    "networks",
    "last_seen",
    "status",
]


def _validate(entry: dict) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in entry]
    if missing:
        raise ValueError(
            "provider entry missing required fields: " + ", ".join(missing)
        )
    if not isinstance(entry["provider_id"], str) or entry["provider_id"].strip() == "":
        raise ValueError("provider_id must be a non-empty string")
    if not isinstance(entry["networks"], list):
        raise ValueError("networks must be a list")


def load_registry(path: str = REGISTRY_PATH) -> list:
    """Read the registry file and return a list of provider dicts.

    Raises FileNotFoundError if the file is missing, and ValueError if any
    entry does not match the fixed schema. This is deliberately strict: a
    malformed registry should fail loudly here, not produce silent gaps
    later in the pipeline.
    """
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, list):
        raise ValueError("registry file must contain a JSON list")

    seen_ids = set()
    for entry in data:
        _validate(entry)
        if entry["provider_id"] in seen_ids:
            raise ValueError(
                "duplicate provider_id in registry: " + entry["provider_id"]
            )
        seen_ids.add(entry["provider_id"])

    return data


def save_registry(entries: list, path: str = REGISTRY_PATH) -> None:
    """Write the registry back to disk. Validates before writing so a bad
    in-memory edit never corrupts the file on disk."""
    for entry in entries:
        _validate(entry)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def get_provider(provider_id: str, path: str = REGISTRY_PATH):
    """Return one provider entry by id, or None if not found."""
    for entry in load_registry(path):
        if entry["provider_id"] == provider_id:
            return entry
    return None


def add_provider(
    provider_id: str,
    label: str,
    facilitator_base_url: str,
    supported_url=None,
    verify_url=None,
    settle_url=None,
    known_declaration_url=None,
    networks=None,
    path: str = REGISTRY_PATH,
) -> dict:
    """Add a brand new provider to the registry with no probe history yet.
    status starts as None because no outcome has been measured. Raises if
    the provider_id already exists, since silently overwriting a measured
    provider would erase real history."""
    entries = load_registry(path)
    if any(e["provider_id"] == provider_id for e in entries):
        raise ValueError("provider_id already exists: " + provider_id)

    entry = {
        "provider_id": provider_id,
        "label": label,
        "facilitator_base_url": facilitator_base_url,
        "supported_url": supported_url,
        "verify_url": verify_url,
        "settle_url": settle_url,
        "known_declaration_url": known_declaration_url,
        "networks": networks if networks is not None else [],
        "last_seen": None,
        "status": None,
    }
    _validate(entry)
    entries.append(entry)
    save_registry(entries, path)
    _push_provider_to_api(entry)
    return entry


def update_status(provider_id: str, status: str, path: str = REGISTRY_PATH) -> dict:
    """Update a provider's last known status and last_seen timestamp. This
    is meant to be called by LATER stages (A3 onward) after a real probe
    result comes back, never called speculatively."""
    entries = load_registry(path)
    for entry in entries:
        if entry["provider_id"] == provider_id:
            entry["status"] = status
            entry["last_seen"] = datetime.now(timezone.utc).isoformat()
            save_registry(entries, path)
            try:
                import database as db_module
                if db_module.DB_PATH.exists():
                    db_module.upsert_provider(entry)
            except Exception:
                pass

            _push_provider_to_api(entry)
            return entry
    raise ValueError("provider_id not found: " + provider_id)


def _push_provider_to_api(entry: dict) -> None:
    """Optionally push provider to a remote API endpoint."""
    api_url = os.environ.get("X402_API_URL", "").rstrip("/")
    if not api_url:
        print("[API] X402_API_URL not set, skipping provider push")
        return
    try:
        import requests
        response = requests.post(
            f"{api_url}/ingest/providers",
            json={"providers": [entry]},
            timeout=10,
        )
        print("[API] Pushed provider: " + str(response.status_code))
    except Exception as exc:
        print("[API] Failed to push provider: " + str(exc))


if __name__ == "__main__":
    # Manual sanity check only. Not part of the pipeline.
    loaded = load_registry()
    print("Loaded " + str(len(loaded)) + " providers:")
    for item in loaded:
        print("  " + item["provider_id"] + " -> " + str(item["status"]))
