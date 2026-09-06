"""Build the exact 16-element argument list for X402Auditor.audit() with status validation."""


def build_claim(
    facilitator: str,
    rpc_url: str,
    claim_success: str,
    claim_transaction: str,
    claim_network: str,
    claim_payer: str,
    claim_amount: str,
    claim_valid_before: str,
    requirements: dict,
    claim_source: str,
    anchor_block: str = "",
) -> dict:
    """Build the exact 16-element argument list for X402Auditor.audit().

    Args:
        facilitator: provider label from the registry
        rpc_url: JSON-RPC endpoint the contract should read from
        claim_success: "true" / "false" / "" (empty is allowed)
        claim_transaction: normalized 32-byte hash or ""
        claim_network: network label from the settlement claim or ""
        claim_payer: payer from the settlement claim or ""
        claim_amount: amount from the settlement claim or ""
        claim_valid_before: signed validBefore timestamp or ""
        requirements: payment requirements dict from the 402 response
        claim_source: "self_probe" / "discovered_only" / "reported" / ""
        anchor_block: block number hex from A6, or ""

    Returns a dict:
        status: CLAIM_READY / REQUIREMENTS_INCOMPLETE / UNKNOWN_CLAIM_SOURCE
        reason: None on success, otherwise a short machine-readable reason
        claim: the 16-element list when status is CLAIM_READY, else None
        fields: dict of the named fields, for logging
    """
    # claim_source must be valid if non-empty. Empty is allowed (the
    # contract defaults it to "discovered_only", handoff section 9). Any
    # other value would produce UNDETERMINED_UNKNOWN_CLAIM_SOURCE inside
    # the contract, which is a waste of a consensus round, so refuse it
    # here before any gas is spent.
    source = claim_source.strip().lower() if isinstance(claim_source, str) else ""
    allowed_sources = {"self_probe", "discovered_only", "reported"}
    if source != "" and source not in allowed_sources:
        return {
            "status": "UNKNOWN_CLAIM_SOURCE",
            "reason": (
                "claim_source must be one of self_probe, discovered_only, "
                "reported, or empty. Got: "
                + repr(claim_source)
            ),
            "claim": None,
            "fields": _fields_dict(
                facilitator, rpc_url, claim_success, claim_transaction,
                claim_network, claim_payer, claim_amount, claim_valid_before,
                requirements, claim_source, anchor_block,
            ),
        }

    # requirements must contain the five fields the contract's judge()
    # reads from the claim. Missing one is a build error, not a silent
    # empty-string, because a claim without req_scheme cannot possibly be
    # judged correctly.
    required_req_fields = ["scheme", "network", "payTo", "asset", "maxAmountRequired"]
    if not isinstance(requirements, dict):
        return {
            "status": "REQUIREMENTS_INCOMPLETE",
            "reason": "requirements is not a dict",
            "claim": None,
            "fields": _fields_dict(
                facilitator, rpc_url, claim_success, claim_transaction,
                claim_network, claim_payer, claim_amount, claim_valid_before,
                requirements, claim_source, anchor_block,
            ),
        }

    missing = [f for f in required_req_fields if f not in requirements]
    if missing:
        return {
            "status": "REQUIREMENTS_INCOMPLETE",
            "reason": "requirements missing fields: " + ", ".join(missing),
            "claim": None,
            "fields": _fields_dict(
                facilitator, rpc_url, claim_success, claim_transaction,
                claim_network, claim_payer, claim_amount, claim_valid_before,
                requirements, claim_source, anchor_block,
            ),
        }

    claim = [
        str(facilitator),
        str(rpc_url),
        str(claim_success),
        str(claim_transaction),
        str(claim_network),
        str(claim_payer),
        str(claim_amount),
        "",  # claim_error_reason: nothing claimed, nothing to report
        str(claim_valid_before),
        str(requirements["scheme"]),
        str(requirements["network"]),
        str(requirements["payTo"]),
        str(requirements["asset"]),
        str(requirements["maxAmountRequired"]),
        str(anchor_block),
        source,
    ]

    return {
        "status": "CLAIM_READY",
        "reason": None,
        "claim": claim,
        "fields": _fields_dict(
            facilitator, rpc_url, claim_success, claim_transaction,
            claim_network, claim_payer, claim_amount, claim_valid_before,
            requirements, claim_source, anchor_block,
        ),
    }


def _fields_dict(
    facilitator: str,
    rpc_url: str,
    claim_success: str,
    claim_transaction: str,
    claim_network: str,
    claim_payer: str,
    claim_amount: str,
    claim_valid_before: str,
    requirements: dict,
    claim_source: str,
    anchor_block: str,
) -> dict:
    """Mirror the named fields for logging. The contract only receives
    the positional list; this dict is for the off-chain summary so a
    failed claim is debuggable."""
    return {
        "facilitator": facilitator,
        "rpc_url": rpc_url,
        "claim_success": claim_success,
        "claim_transaction": claim_transaction,
        "claim_network": claim_network,
        "claim_payer": claim_payer,
        "claim_amount": claim_amount,
        "claim_valid_before": claim_valid_before,
        "requirements": requirements,
        "claim_source": claim_source,
        "anchor_block": anchor_block,
    }


def _run_self_test() -> None:
    """Offline checks proving this file does what its docstring claims.
    No mocks needed because there is nothing external to mock."""
    checks = []

    # A real self_probe claim, all fields present.
    result = build_claim(
        facilitator="x402org-public",
        rpc_url="https://sepolia.base.org",
        claim_success="true",
        claim_transaction="0x" + "ab" * 32,
        claim_network="eip155:84532",
        claim_payer="0x1d8757aae49cb66adf814ccf26658a3e31a20aa1",
        claim_amount="",
        claim_valid_before="1788264699",
        requirements={
            "scheme": "exact",
            "network": "eip155:84532",
            "payTo": "0x572bb4287aacbd42d647d611459e996ec9c89c52",
            "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            "maxAmountRequired": "10000",
        },
        claim_source="self_probe",
        anchor_block="0x2c1b071",
    )
    expected_claim = [
        "x402org-public",
        "https://sepolia.base.org",
        "true",
        "0x" + "ab" * 32,
        "eip155:84532",
        "0x1d8757aae49cb66adf814ccf26658a3e31a20aa1",
        "",
        "",
        "1788264699",
        "exact",
        "eip155:84532",
        "0x572bb4287aacbd42d647d611459e996ec9c89c52",
        "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "10000",
        "0x2c1b071",
        "self_probe",
    ]
    checks.append((
        'build_claim produces the exact 16-element list in exact order',
        result["status"] == "CLAIM_READY" and result["claim"] == expected_claim,
    ))

    # Empty claim_source is allowed and stays empty (contract defaults it).
    result_empty_source = build_claim(
        facilitator="x402org-public",
        rpc_url="https://sepolia.base.org",
        claim_success="",
        claim_transaction="",
        claim_network="",
        claim_payer="",
        claim_amount="",
        claim_valid_before="",
        requirements={
            "scheme": "exact",
            "network": "eip155:84532",
            "payTo": "0x572bb4287aacbd42d647d611459e996ec9c89c52",
            "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            "maxAmountRequired": "10000",
        },
        claim_source="",
    )
    checks.append((
        'build_claim allows empty claim_source and empty claim fields',
        result_empty_source["status"] == "CLAIM_READY"
        and result_empty_source["claim"][15] == "",
    ))

    # Unknown claim_source is rejected before any gas is spent.
    result_bad_source = build_claim(
        facilitator="f",
        rpc_url="r",
        claim_success="",
        claim_transaction="",
        claim_network="",
        claim_payer="",
        claim_amount="",
        claim_valid_before="",
        requirements={
            "scheme": "exact",
            "network": "eip155:84532",
            "payTo": "0x572bb4287aacbd42d647d611459e996ec9c89c52",
            "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            "maxAmountRequired": "10000",
        },
        claim_source="made_up_source",
    )
    checks.append((
        'build_claim rejects an unknown claim_source before any gas is spent',
        result_bad_source["status"] == "UNKNOWN_CLAIM_SOURCE"
        and result_bad_source["claim"] is None,
    ))

    # Missing requirements field is rejected.
    result_missing_req = build_claim(
        facilitator="f",
        rpc_url="r",
        claim_success="",
        claim_transaction="",
        claim_network="",
        claim_payer="",
        claim_amount="",
        claim_valid_before="",
        requirements={"scheme": "exact"},
        claim_source="self_probe",
    )
    checks.append((
        'build_claim rejects missing requirements fields',
        result_missing_req["status"] == "REQUIREMENTS_INCOMPLETE"
        and result_missing_req["claim"] is None,
    ))

    # Non-dict requirements is rejected.
    result_bad_req = build_claim(
        facilitator="f",
        rpc_url="r",
        claim_success="",
        claim_transaction="",
        claim_network="",
        claim_payer="",
        claim_amount="",
        claim_valid_before="",
        requirements="not-a-dict",
        claim_source="self_probe",
    )
    checks.append((
        'build_claim rejects non-dict requirements',
        result_bad_req["status"] == "REQUIREMENTS_INCOMPLETE"
        and result_bad_req["claim"] is None,
    ))

    # discovered_only is accepted.
    result_discovered = build_claim(
        facilitator="f",
        rpc_url="r",
        claim_success="",
        claim_transaction="0x" + "cd" * 32,
        claim_network="",
        claim_payer="",
        claim_amount="",
        claim_valid_before="",
        requirements={
            "scheme": "exact",
            "network": "eip155:84532",
            "payTo": "0x572bb4287aacbd42d647d611459e996ec9c89c52",
            "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            "maxAmountRequired": "10000",
        },
        claim_source="discovered_only",
        anchor_block="0x2c1b079",
    )
    checks.append((
        'build_claim accepts discovered_only',
        result_discovered["status"] == "CLAIM_READY"
        and result_discovered["claim"][15] == "discovered_only",
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