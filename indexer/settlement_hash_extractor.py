"""Extract and normalize 32-byte settlement transaction hashes from x402 claim and RPC sources."""

import re


# Exactly 64 hex characters after an optional "0x". Ethereum-family
# transaction hashes are 32 bytes, no more, no less. A short or long
# value is not a truncated or padded hash, it is a different, wrong
# value, and treating it as "close enough" would let a malformed or
# partially-copied hash slip into an on-chain audit() call silently.
_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Priority order for locating a hash inside a settlement claim dict.
# "transaction" is the field name the x402 SettlementResponse spec uses.
# "transactionHash" and "txHash" are defensive fallbacks kept from earlier
# code. "hash" is a last-resort fallback that has never been observed in
# this project's real captured data. It is included because rejecting an
# unfamiliar field name outright would be a design choice, not a measured
# fact, and this file should not manufacture a fact it does not have. It
# is listed last on purpose so a real observed field name always wins over it.
CLAIM_HASH_FIELDS = ["transaction", "transactionHash", "txHash", "hash"]


def normalize_tx_hash(raw_value):
    """Validate and normalize one candidate transaction hash.

    Returns a lowercase 0x-prefixed 66-character string on success, or
    None on ANY failure. Never raises for a malformed value, because a
    malformed hash is an ordinary, expected outcome of reading messy
    external data, not an infrastructure fault. Never pads, truncates, or
    otherwise repairs a value that is not already exactly 32 bytes of hex,
    because doing so would manufacture a hash that was never actually
    observed anywhere.
    """
    if raw_value is None:
        return None

    text = str(raw_value).strip().lower()
    if text == "":
        return None

    body = text[2:] if text.startswith("0x") else text

    if not _HEX64_PATTERN.match(body):
        return None

    return "0x" + body


def extract_hash_from_claim(claim: dict, source_label: str = "settlement_claim") -> dict:
    """Look for a transaction hash inside a settlement claim dict, trying
    CLAIM_HASH_FIELDS in priority order.

    Returns a dict, never a bare string, because a caller needs to know
    not just the answer but how confident it is and what was rejected
    along the way:

        hash            normalized hash string, or None if nothing usable
                        was found
        fieldUsed       which field name produced the hash, or None
        sourceLabel     echoed back from the argument, for logging
        rejectedFields  {field_name: raw_value} for every field that WAS
                        present but did NOT normalize successfully, so a
                        malformed value is visible, not silently treated
                        the same as an absent one
        reason          short machine-readable explanation when hash is
                        None
    """
    if not isinstance(claim, dict):
        return {
            "hash": None,
            "fieldUsed": None,
            "sourceLabel": source_label,
            "rejectedFields": {},
            "reason": "claim_not_a_dict",
        }

    rejected_fields = {}

    for field_name in CLAIM_HASH_FIELDS:
        if field_name not in claim:
            continue
        raw_value = claim.get(field_name)
        normalized = normalize_tx_hash(raw_value)
        if normalized is not None:
            return {
                "hash": normalized,
                "fieldUsed": field_name,
                "sourceLabel": source_label,
                "rejectedFields": rejected_fields,
                "reason": None,
            }
        # Present, but did not survive normalization. Recorded, not
        # skipped past silently, because a malformed field is evidence
        # of something (a truncated copy, a wrong type, an upstream bug)
        # that an absent field is not.
        if raw_value is not None and str(raw_value).strip() != "":
            rejected_fields[field_name] = raw_value

    reason = "no_known_field_present" if not rejected_fields else "all_present_fields_malformed"
    return {
        "hash": None,
        "fieldUsed": None,
        "sourceLabel": source_label,
        "rejectedFields": rejected_fields,
        "reason": reason,
    }


def extract_hash_from_rpc_discovery(found_hash) -> dict:
    """Normalize the single hash string rpc_transfer_scanner.py's
    find_settlement_transfer() returns. That function already only ever
    returns None or a lowercase 0x-prefixed hash pulled straight from an
    RPC log's transactionHash field, so in the ordinary case this is a
    pass-through. It is not skipped, because "the upstream function is
    already careful" is exactly the kind of assumption that quietly stops
    being true the day someone changes that function without noticing
    this one depends on its output shape.
    """
    normalized = normalize_tx_hash(found_hash)
    return {
        "hash": normalized,
        "fieldUsed": "rpc_scan_result" if normalized is not None else None,
        "sourceLabel": "discovered_only",
        "rejectedFields": {} if normalized is not None or found_hash is None else {
            "rpc_scan_result": found_hash
        },
        "reason": None if normalized is not None else (
            "no_hash_found" if found_hash is None else "rpc_result_malformed"
        ),
    }


def extract_from_reported_claim(claim: dict) -> dict:
    """Deliberately unbuilt. No third-party report source is wired into
    this indexer yet (provider_discovery.py's DISCOVERY_SOURCES are both
    still NOT_IMPLEMENTED). Writing a field-matching routine against a
    shape nobody has actually captured would be a guess wearing the
    clothes of a measurement. Wire this up only after a real "reported"
    source exists and its actual JSON shape has been captured and read,
    the same way every other shape in this file was.
    """
    raise NotImplementedError(
        "extract_from_reported_claim is intentionally not implemented. "
        "No 'reported' evidence source is wired into this indexer yet. "
        "See provider_discovery.py's DISCOVERY_SOURCES for the same "
        "NOT_IMPLEMENTED stance on external, unread data sources."
    )


def extract_from_batch_claim(claim: dict) -> dict:
    """Deliberately unbuilt. The batch settlement scheme is explicitly out
    of this project's first cut, same status as the 'upto' scheme. A
    batch claim can carry MANY settlement hashes under one claim. Picking
    one and discarding the rest here would silently undercount. This
    stays unimplemented until batch evidence is actually captured and its
    real shape is known, not guessed.
    """
    raise NotImplementedError(
        "extract_from_batch_claim is intentionally not implemented. "
        "The batch settlement scheme is untested in this project (see "
        "handoff section 19 and section 7's 'upto' precedent). A batch "
        "claim can contain multiple settlement hashes and this function "
        "must not silently pick one and drop the others."
    )


def extract_settlement_hash(source_type: str, raw_evidence) -> dict:
    """Single dispatch point. source_type MUST be one of the exact
    claim_source values X402Auditor.audit() already accepts
    ("self_probe", "reported", "discovered_only"), plus "batch_settlement"
    for the one scheme this project has explicitly decided not to support
    yet. Reusing that exact vocabulary, instead of inventing a parallel
    one for this file, is the whole point: the code that decides how a
    hash was found and the code that decides what claim_source to write
    for it can never disagree about which evidence class something was,
    because they are reading the same three (four) words.

    Raises ValueError on an unrecognized source_type, the same way
    X402Auditor.audit() itself refuses an unknown claim_source rather
    than guessing (x402_auditor_v8.py, the UNDETERMINED_UNKNOWN_CLAIM_SOURCE
    path). Raises NotImplementedError for the two evidence classes this
    project has not built support for yet, so a caller cannot accidentally
    treat "not implemented" the same as "implemented, found nothing."
    """
    label = source_type.strip().lower() if isinstance(source_type, str) else ""

    if label == "self_probe":
        return extract_hash_from_claim(raw_evidence, source_label="self_probe")
    if label == "discovered_only":
        return extract_hash_from_rpc_discovery(raw_evidence)
    if label == "reported":
        return extract_from_reported_claim(raw_evidence)
    if label == "batch_settlement":
        return extract_from_batch_claim(raw_evidence)

    raise ValueError(
        "extract_settlement_hash received an unrecognized source_type: "
        + repr(source_type)
        + ". Expected one of: self_probe, discovered_only, reported, "
        "batch_settlement. Refusing to guess which extraction path "
        "applies, the same way X402Auditor.audit() refuses an unknown "
        "claim_source rather than defaulting silently."
    )


def _run_self_test() -> None:
    """Offline, network-free proof this file does what its own docstring
    claims. No mocks needed, because there is nothing external to mock.
    Every check either passes or raises loudly, matching the project's
    standing rule that infrastructure and logic failure must never be
    reported as the same thing, and here there IS no infrastructure to
    fail.
    """
    checks = []

    good_hash = "0x" + "ab" * 32
    checks.append((
        'normalize_tx_hash accepted a well-formed 32-byte hash',
        normalize_tx_hash(good_hash) == good_hash,
    ))
    checks.append((
        'normalize_tx_hash accepted the same hash without 0x prefix',
        normalize_tx_hash(("ab" * 32)) == good_hash,
    ))
    checks.append((
        'normalize_tx_hash accepted mixed-case input and lowercased it',
        normalize_tx_hash("0x" + ("AB" * 32)) == good_hash,
    ))
    checks.append((
        'normalize_tx_hash rejected a 63-character (one short) hash',
        normalize_tx_hash("0x" + "a" * 63) is None,
    ))
    checks.append((
        'normalize_tx_hash rejected a 65-character (one long) hash',
        normalize_tx_hash("0x" + "a" * 65) is None,
    ))
    checks.append((
        'normalize_tx_hash rejected a non-hex character',
        normalize_tx_hash("0x" + "g" * 64) is None,
    ))
    checks.append((
        'normalize_tx_hash rejected empty string',
        normalize_tx_hash("") is None,
    ))
    checks.append((
        'normalize_tx_hash rejected None',
        normalize_tx_hash(None) is None,
    ))

    claim_primary = {"transaction": good_hash, "transactionHash": "0x" + "cd" * 32}
    result_primary = extract_hash_from_claim(claim_primary)
    checks.append((
        'extract_hash_from_claim found "transaction" field first',
        result_primary["hash"] == good_hash and result_primary["fieldUsed"] == "transaction",
    ))

    claim_fallback = {"transactionHash": good_hash}
    result_fallback = extract_hash_from_claim(claim_fallback)
    checks.append((
        'extract_hash_from_claim fell back to "transactionHash"',
        result_fallback["hash"] == good_hash and result_fallback["fieldUsed"] == "transactionHash",
    ))

    claim_malformed = {"transaction": "not-a-real-hash", "transactionHash": good_hash}
    result_malformed = extract_hash_from_claim(claim_malformed)
    checks.append((
        'extract_hash_from_claim reported the malformed field, did not silently skip it',
        result_malformed["hash"] == good_hash
        and "transaction" in result_malformed["rejectedFields"]
        and result_malformed["rejectedFields"]["transaction"] == "not-a-real-hash",
    ))

    claim_empty = {"network": "base-sepolia"}
    result_empty = extract_hash_from_claim(claim_empty)
    checks.append((
        'extract_hash_from_claim returned no-hash with reason when nothing present',
        result_empty["hash"] is None and result_empty["reason"] == "no_known_field_present",
    ))

    result_rpc = extract_hash_from_rpc_discovery(good_hash)
    checks.append((
        'extract_hash_from_rpc_discovery normalized a found_hash string',
        result_rpc["hash"] == good_hash,
    ))

    dispatch_self_probe = extract_settlement_hash("self_probe", {"transaction": good_hash})
    checks.append((
        'extract_settlement_hash dispatched self_probe to claim extractor',
        dispatch_self_probe["hash"] == good_hash,
    ))

    dispatch_discovered = extract_settlement_hash("discovered_only", good_hash)
    checks.append((
        'extract_settlement_hash dispatched discovered_only to rpc extractor',
        dispatch_discovered["hash"] == good_hash,
    ))

    reported_raised = False
    try:
        extract_settlement_hash("reported", {"transaction": good_hash})
    except NotImplementedError:
        reported_raised = True
    checks.append((
        'extract_settlement_hash raised NotImplementedError for reported',
        reported_raised,
    ))

    batch_raised = False
    try:
        extract_settlement_hash("batch_settlement", {"transactions": [good_hash]})
    except NotImplementedError:
        batch_raised = True
    checks.append((
        'extract_settlement_hash raised NotImplementedError for batch_settlement',
        batch_raised,
    ))

    value_error_raised = False
    try:
        extract_settlement_hash("made_up_source", {})
    except ValueError:
        value_error_raised = True
    checks.append((
        'extract_settlement_hash raised ValueError for an unknown source label',
        value_error_raised,
    ))

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
