# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json

# GenLayer contract: audit an x402 facilitator's on-chain settlement behaviour
# against its declared support.
#
# x402 Facilitator Trust Auditor, version 12
# v12: refuses to guess in a multi-transfer transaction — publishes
#      AMBIGUOUS_MULTIPLE_MATCHES instead of silently choosing the first
#      matching Transfer. v11: distinguishes silence from non-capture.
#      v7: stops lying about chain. v8: stops lying about identity.
#
# Every settled record already carries transactionSender, the address that
# broadcast the settlement and paid the gas. That address is not claimed, it
# is observed on chain, and it cannot be forged by whoever calls audit.
#
# So v8 reports the registry TWICE, from the same records:
#
#   get_registry()              grouped by the CLAIMED label
#   get_registry_by_relayer()   grouped by the OBSERVED relayer address
#
# The second one is the authoritative one. The first one is kept because
# humans need names, not hex.
#
# AND THE GAP BETWEEN THEM IS ITSELF PUBLISHED
#
#   labelRelayerConflict   one label, more than one settling relayer.
#                          either the operator rotates keys, or the label is
#                          being used for two different operators
#   relayerLabelConflict   one relayer, more than one label. somebody is
#                          naming the same operator inconsistently, whether
#                          by accident or on purpose
#
# Neither flag is an accusation of fraud. Both are statements that the naming
# cannot be relied on for those records, which is exactly the honest thing to
# publish instead of silently picking a winner.
#
# WHAT THIS STILL DOES NOT SOLVE, STATED PLAINLY
#
# Binding a relayer address to a real world operator is NOT something a
# contract can do from chain data alone. The relayer address is a durable
# fingerprint, nothing more. Attaching a legal identity to it needs an
# external attestation, and that is a separate problem which is NOT being
# quietly smuggled in here.
#
# What v8 does give: the numbers can no longer be moved by typing a
# different name, because the authoritative key is not typed at all.
#
# STORAGE IS UNCHANGED FROM v7
#
# verdicts: DynArray[str], same shape, same contents, append only. Nothing
# in this version writes, only reads and aggregates. That makes an in place
# code upgrade theoretically valid and it is worth testing, because it would
# preserve the existing audit trail instead of restarting it.
#
# PRESERVED FROM EARLIER VERSIONS, DO NOT REGRESS
#   - every function running inside a nondeterministic block is MODULE LEVEL
#     with explicit arguments. no self, no storage, no contract state
#   - __init__ defined explicitly even when empty
#   - every annotation on a public method names its element type
#   - json.dumps with sort_keys=True and separators before strict_eq
#   - all address comparisons lowercase both sides
#   - topics[0] checked against the full 32 byte Transfer topic
#   - infrastructure failure raises and writes nothing
#   - every verdict carries the observed transfers as its exhibit
#   - the judgement path contains no language model at all
#   - registries are DERIVED from the records, never stored
#   - absence is only final when a block past the EIP-3009 deadline exists
#   - PENDING is never folded into rejected
#   - the current head is never read, because it is not deterministic
# ---------------------------------------------------------------------------

TRANSFER_SIG = "Transfer(address,address,uint256)"
TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

NO_RELAYER = "NO_RELAYER_OBSERVED"

NETWORK_CHAIN_IDS = {
    "base": 8453,
    "eip155:8453": 8453,
    "base-sepolia": 84532,
    "eip155:84532": 84532,
    "ethereum": 1,
    "mainnet": 1,
    "eip155:1": 1,
    "polygon": 137,
    "eip155:137": 137,
    "avalanche": 43114,
    "eip155:43114": 43114,
}

UPTO_SCHEMES = ["upto", "permit2-upto"]


def hex_to_bytes(value: str) -> bytes:
    clean = value[2:] if value.startswith("0x") else value
    if len(clean) % 2 == 1:
        clean = "0" + clean
    return bytes.fromhex(clean)


def hex_to_int_or_none(value: str):
    try:
        return int(value, 16)
    except Exception:
        return None


def rpc_call(rpc_url: str, method: str, params: list) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    })
    res = gl.nondet.web.request(
        rpc_url,
        method="POST",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    if res.status != 200:
        raise Exception("rpc_status_" + str(res.status))
    if res.body is None:
        raise Exception("rpc_empty_body")
    parsed = json.loads(res.body.decode("utf-8"))
    if "error" in parsed:
        raise Exception(
            "rpc_error_" + json.dumps(parsed["error"], sort_keys=True)
        )
    if "result" not in parsed:
        raise Exception("rpc_no_result_field")
    return parsed


def block_timestamp_or_none(rpc_url: str, block_hex: str):
    # Reads one specific block by number. Immutable data, safe for strict_eq.
    reply = rpc_call(rpc_url, "eth_getBlockByNumber", [block_hex, False])
    result = reply["result"]
    if result is None:
        return None
    return hex_to_int_or_none(str(result.get("timestamp", "")))


def fetch_evidence(rpc_url: str, tx_hash: str, anchor_block: str) -> str:
    chain_reply = rpc_call(rpc_url, "eth_chainId", [])
    chain_id_hex = str(chain_reply["result"]).lower()

    anchor_requested = anchor_block != ""
    anchor_number = None
    anchor_timestamp = None
    if anchor_requested:
        anchor_number = int(anchor_block)
        anchor_hex = "0x" + format(anchor_number, "x")
        anchor_timestamp = block_timestamp_or_none(rpc_url, anchor_hex)

    receipt_reply = rpc_call(rpc_url, "eth_getTransactionReceipt", [tx_hash])
    result = receipt_reply["result"]

    # The receipt carries events. It does NOT carry the call data, and the
    # call data is the only place where an x402 settlement identifies itself
    # as one. A matching Transfer event proves a payment happened. It does
    # not prove the payment was x402, and anyone can emit a matching one.
    tx_to = ""
    tx_input = ""
    if result is not None:
        tx_reply = rpc_call(rpc_url, "eth_getTransactionByHash", [tx_hash])
        tx = tx_reply["result"]
        if tx is not None:
            tx_to = str(tx.get("to", "")).lower()
            tx_input = str(tx.get("input", "")).lower()

    picked = {
        "chainIdHex": chain_id_hex,
        "transactionHash": tx_hash.lower(),
        "anchorRequested": anchor_requested,
        "anchorBlockNumber": anchor_number,
        "anchorFound": anchor_timestamp is not None,
        "anchorTimestamp": anchor_timestamp,
    }

    if result is None:
        picked["found"] = False
        return json.dumps(picked, sort_keys=True, separators=(",", ":"))

    logs = []
    for entry in (result.get("logs") or []):
        logs.append({
            "address": str(entry.get("address", "")).lower(),
            "topics": [str(t).lower() for t in (entry.get("topics") or [])],
            "data": str(entry.get("data", "")).lower(),
        })

    block_hex = str(result.get("blockNumber", "")).lower()

    picked["found"] = True
    picked["transactionHash"] = str(result.get("transactionHash", "")).lower()
    picked["blockNumber"] = block_hex
    picked["status"] = str(result.get("status", "")).lower()
    picked["from"] = str(result.get("from", "")).lower()
    picked["to"] = str(result.get("to", "")).lower()
    picked["logs"] = logs
    picked["txTo"] = tx_to
    picked["txInput"] = tx_input
    picked["settlementTimestamp"] = block_timestamp_or_none(rpc_url, block_hex)

    return json.dumps(picked, sort_keys=True, separators=(",", ":"))


def decode_transfer(log: dict) -> dict:
    topics = log.get("topics") or []
    if len(topics) < 3:
        return {}
    if topics[0] != TRANSFER_TOPIC0:
        return {}
    sender = str(gl.evm.decode(Address, hex_to_bytes(topics[1]))).lower()
    receiver = str(gl.evm.decode(Address, hex_to_bytes(topics[2]))).lower()
    value = int(gl.evm.decode(u256, hex_to_bytes(log.get("data", "0x0"))))
    return {
        "token": str(log.get("address", "")).lower(),
        "from": sender,
        "to": receiver,
        "amount": value,
    }


def selector_hex(signature: str) -> str:
    # Computed, never hardcoded. Keccak256 was verified in production against
    # the known Transfer topic, so a guessed constant would be the only weak
    # link here. hexdigest returns a plain lowercase string with no 0x.
    return "0x" + Keccak256(signature.encode("utf-8")).hexdigest()[:8]


def decode_authorization(input_hex: str) -> dict:
    # EIP-3009 transferWithAuthorization. Two accepted forms exist: one with
    # split v, r, s and one with a packed signature. The first six arguments
    # are identical and statically encoded in both, so one decoder covers
    # them. Everything after those six is signature material we do not need,
    # because the chain already validated it by executing the call.
    out = {"selector": "", "methodMatch": False}
    data = input_hex[2:] if input_hex.startswith("0x") else input_hex
    if len(data) < 8:
        return out

    selector = "0x" + data[:8].lower()
    out["selector"] = selector

    accepted = [
        selector_hex(
            "transferWithAuthorization(address,address,uint256,uint256,"
            + "uint256,bytes32,uint8,bytes32,bytes32)"
        ),
        selector_hex(
            "transferWithAuthorization(address,address,uint256,uint256,"
            + "uint256,bytes32,bytes)"
        ),
    ]
    if selector not in accepted:
        return out

    body = data[8:]
    if len(body) < 384:
        return out

    words = []
    index = 0
    while index < 6:
        words.append(body[index * 64:(index + 1) * 64])
        index = index + 1

    out["methodMatch"] = True
    out["authFrom"] = "0x" + words[0][24:]
    out["authTo"] = "0x" + words[1][24:]
    out["authValue"] = int(words[2], 16)
    out["authValidAfter"] = int(words[3], 16)
    out["authValidBefore"] = int(words[4], 16)
    out["authNonce"] = "0x" + words[5]
    return out


def parse_int_or_none(text: str):
    if text == "":
        return None
    try:
        return int(text)
    except Exception:
        return None


def claim_summary(facilitator: str, claim: dict, req: dict) -> dict:
    return {
        "facilitator": facilitator,
        "claimSource": claim["source"],
        "claimSuccess": claim["success"],
        "claimTransaction": claim["transaction"],
        "claimNetwork": claim["network"],
        "claimPayer": claim["payer"],
        "claimAmount": claim["amount"],
        "claimErrorReason": claim["errorReason"],
        "claimValidBefore": claim["validBefore"],
        "reqScheme": req["scheme"],
        "reqNetwork": req["network"],
        "reqPayTo": req["payTo"],
        "reqAsset": req["asset"],
        "reqMaxAmountRequired": req["maxAmountRequired"],
    }


def amount_acceptable(scheme: str, observed: int, required) -> bool:
    if required is None:
        return False
    if scheme in UPTO_SCHEMES:
        if observed <= 0:
            return False
        return observed <= required
    return observed == required


def judge_absence(record: dict, evidence: dict, claim: dict) -> dict:
    # The only path that can produce a permanent accusation, so it is the
    # most conservative path in the contract. Every exit before the last one
    # refuses to judge.
    valid_before = parse_int_or_none(claim["validBefore"])

    if not evidence.get("anchorRequested"):
        record["verdict"] = "PENDING_ABSENCE_NOT_YET_FINAL"
        return record
    if valid_before is None:
        record["verdict"] = "PENDING_ABSENCE_NOT_YET_FINAL"
        return record
    if not evidence.get("anchorFound"):
        record["verdict"] = "UNDETERMINED_ANCHOR_BLOCK_NOT_FOUND"
        return record

    anchor_ts = evidence.get("anchorTimestamp")
    if anchor_ts is None:
        record["verdict"] = "UNDETERMINED_ANCHOR_TIMESTAMP_UNREADABLE"
        return record
    if anchor_ts <= valid_before:
        record["verdict"] = "PENDING_AUTHORIZATION_STILL_VALID"
        return record

    # Proven: a block past the authorisation deadline exists and the receipt
    # is still absent. EIP-3009 makes that authorisation permanently dead.
    record["absenceFinal"] = True

    if claim["success"] == "false":
        record["verdict"] = "CONFIRMED_FAILURE_NOT_ON_CHAIN"
    elif claim["success"] == "":
        record["verdict"] = "UNREMARKABLE_NO_CLAIM_AND_NOTHING_ON_CHAIN"
    else:
        record["verdict"] = "REJECTED_NO_SUCH_TRANSACTION"
    return record


def judge(evidence_json: str, facilitator: str, claim: dict, req: dict) -> dict:
    evidence = json.loads(evidence_json)
    record = claim_summary(facilitator, claim, req)
    record["chainIdHex"] = evidence.get("chainIdHex", "")
    record["anchorBlock"] = evidence.get("anchorBlockNumber")
    record["anchorTimestamp"] = evidence.get("anchorTimestamp")

    declared = NETWORK_CHAIN_IDS.get(claim["network"])
    if declared is None:
        declared = NETWORK_CHAIN_IDS.get(req["network"])
    observed_chain = hex_to_int_or_none(str(evidence.get("chainIdHex", "")))
    record["declaredChainId"] = declared
    record["observedChainId"] = observed_chain

    if declared is None:
        record["verdict"] = "UNDETERMINED_UNKNOWN_NETWORK_LABEL"
        return record
    if observed_chain is None:
        record["verdict"] = "UNDETERMINED_RPC_CHAIN_UNREADABLE"
        return record
    if observed_chain != declared:
        record["verdict"] = "UNDETERMINED_RPC_CHAIN_MISMATCH"
        return record

    if not evidence.get("found"):
        return judge_absence(record, evidence, claim)

    record["onChainStatus"] = evidence.get("status", "")
    record["blockNumber"] = evidence.get("blockNumber", "")
    record["settlementTimestamp"] = evidence.get("settlementTimestamp")
    record["transactionSender"] = evidence.get("from", "")

    auth = decode_authorization(str(evidence.get("txInput", "")))
    record["txTo"] = evidence.get("txTo", "")
    record["authSelector"] = auth.get("selector", "")
    record["authMethodMatch"] = auth.get("methodMatch", False)
    if auth.get("methodMatch"):
        record["authFrom"] = auth.get("authFrom")
        record["authTo"] = auth.get("authTo")
        record["authValue"] = auth.get("authValue")
        record["authValidAfter"] = auth.get("authValidAfter")
        record["authValidBefore"] = auth.get("authValidBefore")
        record["authNonce"] = auth.get("authNonce")

    if evidence.get("status") != "0x1":
        if claim["success"] == "false":
            record["verdict"] = "CONFIRMED_FAILURE_TRANSACTION_REVERTED"
        else:
            record["verdict"] = "REJECTED_TRANSACTION_REVERTED"
        return record

    observed = []
    for log in (evidence.get("logs") or []):
        decoded = decode_transfer(log)
        if decoded:
            observed.append(decoded)
    record["transferCount"] = len(observed)
    record["observed"] = observed

    if len(observed) == 0:
        record["verdict"] = "REJECTED_NO_TRANSFER_EVENT"
        return record

    candidates = []
    for transfer in observed:
        if transfer["token"] != req["asset"]:
            continue
        if transfer["to"] != req["payTo"]:
            continue
        candidates.append(transfer)

    if len(candidates) == 0:
        record["verdict"] = "REJECTED_NO_TRANSFER_TO_REQUIRED_PAYEE"
        return record

    payer_matches = []
    for transfer in candidates:
        if claim["payer"] == "":
            payer_matches.append(transfer)
        elif transfer["from"] == claim["payer"]:
            payer_matches.append(transfer)

    if len(payer_matches) == 0:
        record["verdict"] = "REJECTED_PAYER_MISMATCH"
        return record

    required = parse_int_or_none(req["maxAmountRequired"])
    settled = []
    for transfer in payer_matches:
        if amount_acceptable(req["scheme"], transfer["amount"], required):
            settled.append(transfer)

    if len(settled) == 0:
        record["verdict"] = "REJECTED_AMOUNT_DOES_NOT_SATISFY_REQUIREMENT"
        return record

    # v12: more than one transfer in this transaction satisfies the
    # requirement. The current contract used to pick settled[0] silently
    # (by receipt log order), which is precisely the "accidental match"
    # risk the road map warns about (handoff section 7 / 18.26). We never
    # guess: we publish the ambiguity, with every candidate, and we do NOT
    # count any money — none of the candidates is provably THE settlement.
    if len(settled) > 1:
        record["candidateCount"] = len(settled)
        record["candidateAmounts"] = [t["amount"] for t in settled]
        record["settledAmount"] = None
        record["verdict"] = "AMBIGUOUS_MULTIPLE_MATCHES"
        return record

    record["settledAmount"] = settled[0]["amount"]

    # Authorisation layer. Reaching here already proves a payment matching
    # the requirements landed on chain. What follows decides whether that
    # payment is provably an x402 settlement or merely consistent with one,
    # and the difference is published instead of assumed either way.
    if record["authMethodMatch"]:
        if record["txTo"] != req["asset"]:
            record["verdict"] = "REJECTED_AUTHORIZATION_WRONG_TOKEN_CONTRACT"
            return record
        if record["authTo"] != req["payTo"]:
            record["verdict"] = "REJECTED_AUTHORIZATION_PAYEE_MISMATCH"
            return record
        if record["authFrom"] != settled[0]["from"]:
            record["verdict"] = "REJECTED_AUTHORIZATION_PAYER_MISMATCH"
            return record
        if record["authValue"] != settled[0]["amount"]:
            record["verdict"] = "REJECTED_AUTHORIZATION_AMOUNT_MISMATCH"
            return record

        claimed_valid_before = parse_int_or_none(claim["validBefore"])
        if claimed_valid_before is not None:
            if claimed_valid_before != record["authValidBefore"]:
                record["verdict"] = (
                    "REJECTED_CLAIMED_AUTHORIZATION_DOES_NOT_MATCH_CHAIN"
                )
                return record

        # Proven, not inferred: the transaction called the token contract
        # itself through EIP-3009, and every authorised field agrees with
        # the event that the call produced.
        record["x402Proof"] = "EIP3009_AUTHORIZATION_VERIFIED"
    else:
        # Honest downgrade. The payment is real and matches, but nothing
        # here proves it was an x402 settlement rather than an ordinary
        # transfer that happens to look identical. Saying otherwise would
        # be the exact kind of false positive this layer exists to prevent.
        record["x402Proof"] = "TRANSFER_EVENT_ONLY"

    claimed_amount = parse_int_or_none(claim["amount"])
    if claimed_amount is not None:
        if claimed_amount != settled[0]["amount"]:
            record["verdict"] = "REJECTED_CLAIMED_AMOUNT_DOES_NOT_MATCH_CHAIN"
            return record

    if claim["success"] == "false":
        record["verdict"] = "CONTRADICTED_CLAIMED_FAILURE_BUT_CHAIN_SETTLED"
        return record
    if claim["success"] == "":
        # Silence and non capture are two different facts about the world.
        # Only an announcement channel we actually listened to can support
        # the accusation that a facilitator stayed silent. A transaction we
        # merely discovered on chain carries no such evidence, and saying
        # otherwise would defame every operator the indexer ever finds.
        if claim["source"] == "self_probe" or claim["source"] == "reported":
            record["verdict"] = "SETTLED_BUT_NO_SETTLEMENT_RESPONSE_CLAIMED"
        else:
            record["verdict"] = "SETTLED_NO_ANNOUNCEMENT_CAPTURED"
        return record

    record["verdict"] = "CONFIRMED"
    return record


def base_counters() -> dict:
    return {
        "totalRecords": 0,
        "settledOnChain": 0,
        "settledAtomicTotal": 0,
        "confirmed": 0,
        "contradictedFailure": 0,
        "settledNoClaim": 0,
        "settledNoAnnouncementCaptured": 0,
        "legacyUnclassifiedSilence": 0,
        "evidenceSelfProbe": 0,
        "evidenceReported": 0,
        "evidenceDiscoveredOnly": 0,
        "confirmedFailure": 0,
        "rejected": 0,
        "pending": 0,
        "undetermined": 0,
        "unremarkable": 0,
        "duplicateAudits": 0,
        "ambiguous": 0,
        "settledTxHashes": [],
    }


def empty_label_counters(name: str) -> dict:
    counters = base_counters()
    counters["facilitator"] = name
    counters["relayers"] = []
    return counters


def empty_relayer_counters(relayer: str) -> dict:
    counters = base_counters()
    counters["relayer"] = relayer
    counters["labels"] = []
    return counters


def fold_common(counters: dict, record: dict) -> dict:
    # Identical counting rules for both groupings, so the two registries are
    # directly comparable. Any divergence between them is a naming problem,
    # never an arithmetic one.
    verdict = str(record.get("verdict", ""))
    source = str(record.get("claimSource", ""))
    counters["totalRecords"] = counters["totalRecords"] + 1

    # Evidence class is published, never averaged away. A number produced by
    # probing the facilitator ourselves and a number produced by a third
    # party report are not the same kind of number, and a reader who cannot
    # tell them apart is being misled by the average.
    if source == "self_probe":
        counters["evidenceSelfProbe"] = counters["evidenceSelfProbe"] + 1
    elif source == "reported":
        counters["evidenceReported"] = counters["evidenceReported"] + 1
    elif source == "discovered_only":
        counters["evidenceDiscoveredOnly"] = (
            counters["evidenceDiscoveredOnly"] + 1
        )

    # Money is a property of the chain, not of the claim. Two audits of the
    # same transaction are two claims about one settlement, so the second one
    # must not add money again. v8 counted claims and inflated a real relayer
    # from 40000 to 50000 atomic units. That was a false number, so it is
    # fixed here. The duplicate is still counted, under its own name, because
    # hiding it would be a second lie.
    settled_amount = record.get("settledAmount")
    tx_key = str(record.get("claimTransaction", ""))
    if settled_amount is not None:
        if tx_key != "" and tx_key in counters["settledTxHashes"]:
            counters["duplicateAudits"] = counters["duplicateAudits"] + 1
        else:
            counters["settledOnChain"] = counters["settledOnChain"] + 1
            counters["settledAtomicTotal"] = (
                counters["settledAtomicTotal"] + int(settled_amount)
            )
            if tx_key != "":
                counters["settledTxHashes"].append(tx_key)

    if verdict == "CONFIRMED":
        counters["confirmed"] = counters["confirmed"] + 1
    elif verdict == "CONTRADICTED_CLAIMED_FAILURE_BUT_CHAIN_SETTLED":
        counters["contradictedFailure"] = counters["contradictedFailure"] + 1
    elif verdict == "AMBIGUOUS_MULTIPLE_MATCHES":
        # v12: the transaction carried more than one satisfying Transfer.
        # It is not a confirmation (we cannot name THE settlement) and not
        # a rejection (money clearly moved). It is counted under its own
        # name, and no settledAmount is recorded so no money is counted.
        counters["ambiguous"] = counters["ambiguous"] + 1
    elif verdict == "SETTLED_BUT_NO_SETTLEMENT_RESPONSE_CLAIMED":
        # Records written before v11 carry no evidence class, so for them
        # this verdict cannot be read as an accusation. Storage is never
        # rewritten, but a claim that was never justified is not counted as
        # guilt either. It is counted, under its own name, in the open.
        if source == "":
            counters["legacyUnclassifiedSilence"] = (
                counters["legacyUnclassifiedSilence"] + 1
            )
        else:
            counters["settledNoClaim"] = counters["settledNoClaim"] + 1
    elif verdict == "SETTLED_NO_ANNOUNCEMENT_CAPTURED":
        counters["settledNoAnnouncementCaptured"] = (
            counters["settledNoAnnouncementCaptured"] + 1
        )
    elif verdict.startswith("CONFIRMED_FAILURE"):
        counters["confirmedFailure"] = counters["confirmedFailure"] + 1
    elif verdict.startswith("PENDING_"):
        counters["pending"] = counters["pending"] + 1
    elif verdict.startswith("REJECTED_"):
        counters["rejected"] = counters["rejected"] + 1
    elif verdict.startswith("UNREMARKABLE_"):
        counters["unremarkable"] = counters["unremarkable"] + 1
    elif verdict.startswith("UNDETERMINED_"):
        counters["undetermined"] = counters["undetermined"] + 1
    elif verdict.startswith("UNVERIFIABLE_"):
        counters["undetermined"] = counters["undetermined"] + 1

    return counters


def honesty_pct(counters: dict):
    # Measured only where a claim existed AND the chain settled. Anything
    # else would let silence inflate or deflate the score. Integer division,
    # no floats, so validators compare identical text. None when the
    # denominator is zero, never 0 or 100, because an absent measurement must
    # not read as a perfect or a failing score.
    denominator = counters["confirmed"] + counters["contradictedFailure"]
    if denominator == 0:
        return None
    return (counters["confirmed"] * 100) // denominator


def resolved_transactions(records: list) -> list:
    # A transaction is resolved once any non pending record exists for it.
    resolved = []
    for record in records:
        verdict = str(record.get("verdict", ""))
        tx = str(record.get("claimTransaction", ""))
        if tx == "":
            continue
        if verdict.startswith("PENDING_"):
            continue
        if tx not in resolved:
            resolved.append(tx)
    return resolved


def live_records(raw_records: list) -> list:
    # Decodes storage and drops pending records that a later record already
    # resolved. The dropped records stay in storage forever, because the
    # audit trail must show the wait. Only the aggregate ignores them.
    records = []
    for raw in raw_records:
        try:
            records.append(json.loads(raw))
        except Exception:
            continue

    resolved = resolved_transactions(records)

    kept = []
    for record in records:
        verdict = str(record.get("verdict", ""))
        tx = str(record.get("claimTransaction", ""))
        if verdict.startswith("PENDING_") and tx in resolved:
            continue
        kept.append(record)
    return kept


def build_registry(raw_records: list) -> list:
    grouped = {}
    names = []
    for record in live_records(raw_records):
        name = str(record.get("facilitator", "unlabeled"))
        if name not in grouped:
            grouped[name] = empty_label_counters(name)
            names.append(name)
        counters = fold_common(grouped[name], record)
        relayer = str(record.get("transactionSender", ""))
        if relayer != "":
            if relayer not in counters["relayers"]:
                counters["relayers"].append(relayer)
        grouped[name] = counters

    out = []
    for name in sorted(names):
        counters = grouped[name]
        counters["relayers"] = sorted(counters["relayers"])
        counters["announcementHonestyPct"] = honesty_pct(counters)
        # One typed label, more than one observed settling address. Not proof
        # of anything by itself, and published rather than hidden.
        counters["labelRelayerConflict"] = len(counters["relayers"]) > 1
        counters.pop("settledTxHashes", None)
        out.append(json.dumps(
            counters, sort_keys=True, separators=(",", ":")
        ))
    return out


def build_relayer_registry(raw_records: list) -> list:
    # The authoritative grouping. The key is observed on chain, so it cannot
    # be forged by whoever calls audit.
    grouped = {}
    keys = []
    for record in live_records(raw_records):
        relayer = str(record.get("transactionSender", ""))
        if relayer == "":
            relayer = NO_RELAYER
        if relayer not in grouped:
            grouped[relayer] = empty_relayer_counters(relayer)
            keys.append(relayer)
        counters = fold_common(grouped[relayer], record)
        label = str(record.get("facilitator", "unlabeled"))
        if label not in counters["labels"]:
            counters["labels"].append(label)
        grouped[relayer] = counters

    out = []
    for key in sorted(keys):
        counters = grouped[key]
        counters["labels"] = sorted(counters["labels"])
        counters["announcementHonestyPct"] = honesty_pct(counters)
        # One observed settling address, more than one typed name. Somebody
        # is naming this operator inconsistently.
        counters["relayerLabelConflict"] = len(counters["labels"]) > 1
        counters.pop("settledTxHashes", None)
        out.append(json.dumps(
            counters, sort_keys=True, separators=(",", ":")
        ))
    return out


class X402Auditor(gl.Contract):
    verdicts: DynArray[str]

    def __init__(self) -> None:
        pass

    @gl.public.write
    def audit(
        self,
        facilitator: str,
        rpc_url: str,
        claim_success: str,
        claim_transaction: str,
        claim_network: str,
        claim_payer: str,
        claim_amount: str,
        claim_error_reason: str,
        claim_valid_before: str,
        req_scheme: str,
        req_network: str,
        req_pay_to: str,
        req_asset: str,
        req_max_amount_required: str,
        anchor_block: str,
        claim_source: str,
    ) -> None:
        label = facilitator.strip()
        if label == "":
            label = "unlabeled"

        # Conservative default. An unstated evidence class is treated as the
        # weakest one, never as a captured announcement.
        source = claim_source.strip().lower()
        if source == "":
            source = "discovered_only"

        claim = {
            "source": source,
            "success": claim_success.strip().lower(),
            "transaction": claim_transaction.strip().lower(),
            "network": claim_network.strip().lower(),
            "payer": claim_payer.strip().lower(),
            "amount": claim_amount.strip(),
            "errorReason": claim_error_reason.strip(),
            "validBefore": claim_valid_before.strip(),
        }
        req = {
            "scheme": req_scheme.strip().lower(),
            "network": req_network.strip().lower(),
            "payTo": req_pay_to.strip().lower(),
            "asset": req_asset.strip().lower(),
            "maxAmountRequired": req_max_amount_required.strip(),
        }

        anchor = anchor_block.strip()

        # An unknown evidence class is refused rather than guessed, because
        # this single field decides whether silence is allowed to become an
        # accusation. Refusing is written as a record, not raised as a crash.
        if source not in ("self_probe", "reported", "discovered_only"):
            record = claim_summary(label, claim, req)
            record["verdict"] = "UNDETERMINED_UNKNOWN_CLAIM_SOURCE"
            self.verdicts.append(json.dumps(
                record, sort_keys=True, separators=(",", ":")
            ))
            return

        if claim["transaction"] == "":
            record = claim_summary(label, claim, req)
            if claim["success"] == "false":
                record["verdict"] = "CONFIRMED_FAILURE_NO_HASH_CLAIMED"
            else:
                record["verdict"] = "UNVERIFIABLE_NO_TRANSACTION_HASH"
            self.verdicts.append(json.dumps(
                record, sort_keys=True, separators=(",", ":")
            ))
            return

        # Validated here, OUTSIDE the nondeterministic block, so a bad input
        # becomes a recorded verdict instead of a crash inside consensus.
        if anchor != "" and not anchor.isdigit():
            record = claim_summary(label, claim, req)
            record["verdict"] = "UNDETERMINED_ANCHOR_BLOCK_NOT_A_NUMBER"
            self.verdicts.append(json.dumps(
                record, sort_keys=True, separators=(",", ":")
            ))
            return

        endpoint = rpc_url.strip()
        target = claim["transaction"]

        # Plain locals only inside the closure. No self, no storage.
        evidence_json = gl.eq_principle.strict_eq(
            lambda: fetch_evidence(endpoint, target, anchor)
        )

        record = judge(evidence_json, label, claim, req)
        self.verdicts.append(json.dumps(
            record, sort_keys=True, separators=(",", ":")
        ))

    @gl.public.view
    def get_verdicts(self) -> list[str]:
        return list(self.verdicts)

    @gl.public.view
    def get_registry(self) -> list[str]:
        return build_registry(list(self.verdicts))

    @gl.public.view
    def get_registry_by_relayer(self) -> list[str]:
        return build_relayer_registry(list(self.verdicts))
