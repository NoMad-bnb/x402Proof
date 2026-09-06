"""Local JSON evidence cache with deduplication keyed by chain id and transaction hash."""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EVIDENCE_SCHEMA_VERSION = 1

# Resolved at CALL time (not import time) so tests can point the store at
# a temporary file by monkeypatching this constant, while the real
# pipeline keeps writing the project-local file.
DEFAULT_EVIDENCE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "evidence.json"
)


def extract_evidence_key(summary):
    """Derive the natural key (at least chain_id + transaction_hash) from a
    pipeline summary. Returns a dict with chainId and transactionHash (hash
    lowercased), or None when the summary carries no transaction hash at all."""
    if not isinstance(summary, dict):
        return None

    verification = summary.get("verificationRecord")
    if not isinstance(verification, dict):
        verification = {}
    transaction_hash = str(verification.get("transactionHash") or "")

    if not transaction_hash:
        audit_record = summary.get("auditRecord")
        if isinstance(audit_record, dict):
            transaction_hash = str(audit_record.get("claimTransaction") or "")

    if not transaction_hash:
        return None

    chain_id = str(verification.get("chainId") or "")
    if not chain_id:
        requirements = summary.get("requirements")
        if isinstance(requirements, dict):
            chain_id = str(requirements.get("network") or "")

    return {
        "chainId": chain_id,
        "transactionHash": transaction_hash.lower(),
    }


def compute_evidence_digest(summary):
    """sha256 over the SEMANTIC identity of the evidence, deliberately
    excluding volatile fields (anchorBlock, block timestamps, storage
    time). Identical re-runs hash identically; a genuinely different
    evidence state hashes differently."""
    audit_record = summary.get("auditRecord")
    if not isinstance(audit_record, dict):
        audit_record = {}
    semantic = {
        "claimSource": str(audit_record.get("claimSource", "")),
        "evidenceSource": str(summary.get("evidenceSource", "")),
        "verificationStatus": str(summary.get("verificationStatus", "")),
        "auditVerdict": str(summary.get("auditVerdict", "")),
        "declarationVerdict": str(summary.get("declarationVerdict", "")),
    }
    canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def load_evidence(path=None):
    """Read the evidence file. A missing file is an EMPTY store (first
    run); a corrupt one raises loudly instead of pretending to be empty,
    same strictness as provider_registry.load_registry()."""
    target = path if path is not None else DEFAULT_EVIDENCE_PATH
    if not os.path.exists(target):
        return []
    with open(target, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("evidence file must contain a JSON list: " + target)
    return data


def _same_key(record_key, key):
    return (
        str(record_key.get("transactionHash", "")).lower()
        == key["transactionHash"]
        and str(record_key.get("chainId", "")) == key["chainId"]
    )


def find_evidence(chain_id, transaction_hash, path=None):
    """All stored records for one natural key, oldest first. Multiple
    records for the same transaction are expected and preserved: they
    carry different evidence sources or different evidence states."""
    key = {
        "chainId": str(chain_id),
        "transactionHash": str(transaction_hash).lower(),
    }
    found = []
    for record in load_evidence(path):
        if _same_key(record.get("evidenceKey") or {}, key):
            found.append(record)
    return found


def store_evidence(summary, path=None):
    """Append one pipeline summary under its natural key, unless the same
    semantic evidence already exists for that key.

    Returns a dict:
        status: EVIDENCE_STORED / EVIDENCE_DUPLICATE / EVIDENCE_NO_KEY
        evidenceKey, evidenceDigest: what was used (None for NO_KEY)

    The full summary is stored verbatim; nothing inside it is judged,
    trimmed, or rewritten here."""
    key = extract_evidence_key(summary)
    if key is None:
        return {
            "status": "EVIDENCE_NO_KEY",
            "evidenceKey": None,
            "evidenceDigest": None,
        }

    digest = compute_evidence_digest(summary)
    target = path if path is not None else DEFAULT_EVIDENCE_PATH
    existing = load_evidence(target)

    for record in existing:
        if _same_key(record.get("evidenceKey") or {}, key):
            if record.get("evidenceDigest") == digest:
                return {
                    "status": "EVIDENCE_DUPLICATE",
                    "evidenceKey": key,
                    "evidenceDigest": digest,
                }

    entry = {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "storedAt": datetime.now(timezone.utc).isoformat(),
        "evidenceKey": key,
        "evidenceDigest": digest,
        "summary": summary,
    }
    existing.append(entry)

    with open(target, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    # Also store in SQLite if available
    try:
        import database as db_module
        if db_module.DB_PATH.exists():
            db_module.insert_evidence(entry)
    except Exception:
        pass

    # Also push to remote API if configured
    _push_evidence_to_api(entry)

    return {
        "status": "EVIDENCE_STORED",
        "evidenceKey": key,
        "evidenceDigest": digest,
    }


def _push_evidence_to_api(entry: dict) -> None:
    """Optionally push evidence to a remote API endpoint."""
    api_url = os.environ.get("X402_API_URL", "").rstrip("/")
    if not api_url:
        return
    try:
        import requests
        requests.post(
            f"{api_url}/ingest/evidence",
            json={"records": [entry]},
            timeout=10,
        )
    except Exception:
        pass


def _run_self_test() -> None:
    """Offline, file-system proof this file does what its own docstring
    claims. Uses a temporary evidence file so the real project-local
    evidence.json is never touched."""
    import tempfile

    checks = []

    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        temp_path = handle.name
        handle.write("[]\n")

    try:
        # 1. extract_evidence_key returns a dict with chainId + tx hash
        #    when present.
        summary_with_key = {
            "verificationRecord": {
                "transactionHash": "0x" + "ab" * 32,
                "chainId": "0x14a34",
            }
        }
        key = extract_evidence_key(summary_with_key)
        checks.append((
            "extract_evidence_key returns chainId + transactionHash",
            key == {
                "chainId": "0x14a34",
                "transactionHash": "0x" + "ab" * 32,
            },
        ))

        # 2. extract_evidence_key falls back to auditRecord.claimTransaction
        #    and requirements.network when verificationRecord is sparse.
        summary_fallback = {
            "auditRecord": {"claimTransaction": "0x" + "cd" * 32},
            "requirements": {"network": "eip155:84532"},
        }
        key_fallback = extract_evidence_key(summary_fallback)
        checks.append((
            "extract_evidence_key falls back to auditRecord + requirements",
            key_fallback == {
                "chainId": "eip155:84532",
                "transactionHash": "0x" + "cd" * 32,
            },
        ))

        # 3. extract_evidence_key returns None when no transaction hash
        #    exists anywhere.
        key_none = extract_evidence_key({"verificationRecord": {}})
        checks.append((
            "extract_evidence_key returns None when no hash is present",
            key_none is None,
        ))

        # 4. compute_evidence_digest is stable across identical summaries.
        summary_a = {
            "auditRecord": {"claimSource": "self_probe"},
            "evidenceSource": "header_capture",
            "verificationStatus": "VERIFIED",
            "auditVerdict": "CONFIRMED",
            "declarationVerdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR",
        }
        summary_b = {
            "auditRecord": {"claimSource": "self_probe"},
            "evidenceSource": "header_capture",
            "verificationStatus": "VERIFIED",
            "auditVerdict": "CONFIRMED",
            "declarationVerdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR",
        }
        checks.append((
            "compute_evidence_digest is stable for identical summaries",
            compute_evidence_digest(summary_a)
            == compute_evidence_digest(summary_b),
        ))

        # 5. compute_evidence_digest differs when semantic fields differ.
        summary_c = {
            "auditRecord": {"claimSource": "self_probe"},
            "evidenceSource": "header_capture",
            "verificationStatus": "VERIFIED",
            "auditVerdict": "CONFIRMED",
            "declarationVerdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR",
        }
        summary_d = {
            "auditRecord": {"claimSource": "discovered_only"},
            "evidenceSource": "rpc_fallback",
            "verificationStatus": "VERIFIED",
            "auditVerdict": "CONFIRMED",
            "declarationVerdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR",
        }
        checks.append((
            "compute_evidence_digest changes when semantics change",
            compute_evidence_digest(summary_c)
            != compute_evidence_digest(summary_d),
        ))

        # 6. load_evidence returns [] for a missing file.
        missing_path = os.path.join(tempfile.gettempdir(), "x402_evidence_missing_" + str(id(checks)) + ".json")
        if os.path.exists(missing_path):
            os.remove(missing_path)
        checks.append((
            "load_evidence returns [] for a missing file",
            load_evidence(missing_path) == [],
        ))

        # 7. store_evidence returns EVIDENCE_STORED on first write.
        summary_store = {
            "verificationRecord": {"transactionHash": "0x" + "ef" * 32, "chainId": "0x14a34"},
            "auditRecord": {"claimSource": "self_probe"},
            "evidenceSource": "header_capture",
            "verificationStatus": "VERIFIED",
            "auditVerdict": "CONFIRMED",
            "declarationVerdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR",
        }
        result_store = store_evidence(summary_store, path=temp_path)
        checks.append((
            "store_evidence returns EVIDENCE_STORED on first write",
            result_store["status"] == "EVIDENCE_STORED"
            and result_store["evidenceKey"] is not None
            and result_store["evidenceDigest"] is not None,
        ))

        # 8. store_evidence returns EVIDENCE_DUPLICATE when key + digest
        #    match an existing record.
        result_dup = store_evidence(summary_store, path=temp_path)
        checks.append((
            "store_evidence returns EVIDENCE_DUPLICATE for identical re-run",
            result_dup["status"] == "EVIDENCE_DUPLICATE",
        ))

        # 9. store_evidence returns EVIDENCE_NO_KEY when the summary has
        #    no transaction hash.
        summary_no_key = {"verificationRecord": {}}
        result_no_key = store_evidence(summary_no_key, path=temp_path)
        checks.append((
            "store_evidence returns EVIDENCE_NO_KEY for keyless summary",
            result_no_key["status"] == "EVIDENCE_NO_KEY"
            and result_no_key["evidenceKey"] is None,
        ))

        # 10. find_evidence returns stored records for a known key.
        found = find_evidence("0x14a34", "0x" + "ef" * 32, path=temp_path)
        checks.append((
            "find_evidence returns the stored record for a known key",
            len(found) == 1
            and found[0]["evidenceKey"]["transactionHash"] == "0x" + "ef" * 32,
        ))

        # 11. Same key + different digest is preserved, not replaced.
        summary_second = {
            "verificationRecord": {"transactionHash": "0x" + "ef" * 32, "chainId": "0x14a34"},
            "auditRecord": {"claimSource": "discovered_only"},
            "evidenceSource": "rpc_fallback",
            "verificationStatus": "VERIFIED",
            "auditVerdict": "CONFIRMED",
            "declarationVerdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR",
        }
        store_evidence(summary_second, path=temp_path)
        found_both = find_evidence("0x14a34", "0x" + "ef" * 32, path=temp_path)
        checks.append((
            "same key + different digest keeps both records",
            len(found_both) == 2,
        ))

        # 12. load_evidence rejects a corrupt non-list file.
        corrupt_path = os.path.join(tempfile.gettempdir(), "x402_evidence_corrupt_" + str(id(checks)) + ".json")
        with open(corrupt_path, "w", encoding="utf-8") as f:
            f.write("not a list\n")
        raised = False
        try:
            load_evidence(corrupt_path)
        except ValueError:
            raised = True
        checks.append((
            "load_evidence raises ValueError for a corrupt non-list file",
            raised,
        ))
    finally:
        for candidate in [temp_path, missing_path, corrupt_path]:
            if candidate and os.path.exists(candidate):
                os.remove(candidate)

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
