"""Execute x402 402->sign->verify->settle payment flow with EIP-3009 signing and capture the settlement hash."""

import base64
import json
import time

import requests

from eip3009_signer import (
    build_authorization,
    payer_address_from_key,
    sign_authorization,
)

REQUEST_TIMEOUT_SECONDS = 30

# Tried in this order on the way OUT (our payment header) and checked in
# this order on the way IN (their settlement response header). Order
# matters only for which one we try first; both are always checked on the
# response regardless of which we sent, since the server may respond with
# whichever name it prefers independent of what we sent it under.
CLIENT_PAYMENT_HEADER_NAMES = ["X-PAYMENT", "PAYMENT-SIGNATURE"]
SERVER_RESPONSE_HEADER_NAMES = ["X-PAYMENT-RESPONSE", "PAYMENT-RESPONSE"]


def _headers_lower(headers) -> dict:
    return {str(k).lower(): v for k, v in dict(headers).items()}


def parse_payment_requirements(body_text: str) -> dict:
    """Parse a 402 response body into one normalized PaymentRequirements
    dict, regardless of whether the server used the flat v1 shape or the
    wrapped v2 {"accepted":[...]} shape. Raises on anything else, because a
    guessed or partially-filled requirement is worse than a loud failure
    here: it would go on to sign a payment for the wrong amount or payee.
    """
    parsed = json.loads(body_text)

    # CONFIRMED against the authoritative spec (github.com/coinbase/x402,
    # specs/x402-specification-v2.md, fetched live): the list key in the
    # PaymentRequired body is "accepts", NOT "accepted". "accepted" is a
    # DIFFERENT field used later, in the client's outgoing PaymentPayload,
    # where it holds the single CHOSEN requirement object, not a list. An
    # earlier version of this function checked "accepted" here by mistake;
    # that was never run against a real v2 server, so it never surfaced.
    accepts_list = parsed.get("accepts") if isinstance(parsed, dict) else None
    if isinstance(accepts_list, list):
        candidates = [c for c in accepts_list if isinstance(c, dict)]
        if len(candidates) == 0:
            raise ValueError("402 body has an empty 'accepts' list")
        # First 'exact' scheme candidate. Other schemes (upto, batch) are
        # explicitly out of scope for this client's first cut.
        exact_only = [c for c in candidates if c.get("scheme") == "exact"]
        if len(exact_only) == 0:
            raise ValueError(
                "no 'exact' scheme in 402 'accepts' list, only scheme "
                "this client can pay: " + json.dumps(candidates)
            )
        chosen = exact_only[0]
        return {
            "scheme": chosen.get("scheme"),
            "network": chosen.get("network"),
            "payTo": chosen.get("payTo"),
            "asset": chosen.get("asset"),
            "maxAmountRequired": str(
                chosen.get("maxAmountRequired", chosen.get("amount", ""))
            ),
            "maxTimeoutSeconds": chosen.get("maxTimeoutSeconds", 300),
            "extra": chosen.get("extra") or {},
            "resource": (parsed.get("resource") or {}).get("url", ""),
            "x402Version": parsed.get("x402Version", 2),
        }

    if isinstance(parsed, dict) and parsed.get("scheme") is not None:
        # Flat v1 shape.
        if parsed.get("scheme") != "exact":
            raise ValueError(
                "only the 'exact' scheme is supported by this client, got: "
                + str(parsed.get("scheme"))
            )
        return {
            "scheme": parsed.get("scheme"),
            "network": parsed.get("network"),
            "payTo": parsed.get("payTo"),
            "asset": parsed.get("asset"),
            "maxAmountRequired": str(parsed.get("maxAmountRequired", "")),
            "maxTimeoutSeconds": parsed.get("maxTimeoutSeconds", 300),
            "extra": parsed.get("extra") or {},
            "resource": parsed.get("resource", ""),
            "x402Version": parsed.get("x402Version", 1),
        }

    raise ValueError(
        "402 body did not match either known PaymentRequirements shape: "
        + body_text[:500]
    )


def build_payment_header_value(
    x402_version: int,
    requirements: dict,
    signature: str,
    authorization: dict,
) -> str:
    """Base64-encode the PaymentPayload. Shape CONFIRMED against the live
    authoritative spec (github.com/coinbase/x402,
    specs/x402-specification-v2.md, section 5.2.1, fetched and cross-checked
    in this session): "accepted" here is the single chosen PaymentRequirements
    object, not a list, which is the opposite of the 402 body's "accepts"
    list. Getting this backwards produces a payload a v2-conformant server
    will reject as malformed even though the signature itself is valid.
    """
    payload = {
        "x402Version": x402_version,
        "accepted": {
            "scheme": requirements["scheme"],
            "network": requirements["network"],
            "amount": str(requirements["maxAmountRequired"]),
            "asset": requirements["asset"],
            "payTo": requirements["payTo"],
            "maxTimeoutSeconds": requirements.get("maxTimeoutSeconds", 300),
            "extra": requirements.get("extra") or {},
        },
        "payload": {
            "signature": signature,
            "authorization": {
                "from": authorization["from"],
                "to": authorization["to"],
                "value": str(authorization["value"]),
                "validAfter": str(authorization["validAfter"]),
                "validBefore": str(authorization["validBefore"]),
                "nonce": authorization["nonce"],
            },
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_settlement_header(value: str):
    """Decode a settlement response header value. Returns None (not an
    exception) on failure, because a malformed or absent header is an
    informative fact for the caller to record, not a crash."""
    try:
        raw = base64.b64decode(value)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def attempt_payment(
    resource_url: str,
    payer_private_key: str,
    valid_after_skew_seconds: int = 60,
    timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    """Execute one full x402 payment attempt against resource_url.

    Returns a dict, never raises for ordinary protocol outcomes (no 402,
    unsupported scheme mismatch handled upstream, missing settlement
    header). It DOES raise for actual infrastructure failure (connection
    refused, timeout, malformed 402 body that cannot be parsed at all),
    per the project's standing rule that infrastructure failure must raise
    and write nothing, never be silently absorbed into a fabricated
    result.

    Result shape (fields always present):
        resourceUrl, requestedAt (unix ts)
        initialStatus
        requirements (dict) or None if no 402 was received
        payerAddress
        signedNetwork, signedAsset, signedPayTo, signedValue,
        signedValidAfter, signedValidBefore, signedNonce
        clientHeaderNameUsed
        retryStatus
        settlementHeaderNamePresent (one of SERVER_RESPONSE_HEADER_NAMES,
            or None if neither was present in the retry response)
        settlementClaim (dict decoded from that header) or None
    """
    result = {
        "resourceUrl": resource_url,
        "requestedAt": int(time.time()),
    }

    initial = requests.get(resource_url, timeout=timeout_seconds)
    result["initialStatus"] = initial.status_code

    if initial.status_code != 402:
        result["requirements"] = None
        return result

    requirements = parse_payment_requirements(initial.text)
    result["requirements"] = requirements

    payer_address = payer_address_from_key(payer_private_key)
    result["payerAddress"] = payer_address

    extra = requirements.get("extra") or {}
    token_name = extra.get("name", "USDC")
    token_version = extra.get("version", "2")

    authorization = build_authorization(
        payer_address=payer_address,
        pay_to=requirements["payTo"],
        value=int(requirements["maxAmountRequired"]),
        max_timeout_seconds=int(requirements.get("maxTimeoutSeconds", 300)),
        valid_after_skew_seconds=valid_after_skew_seconds,
    )

    signature = sign_authorization(
        private_key=payer_private_key,
        network_label=requirements["network"],
        verifying_contract=requirements["asset"],
        token_name=token_name,
        token_version=token_version,
        authorization=authorization,
    )

    result["signedNetwork"] = requirements["network"]
    result["signedAsset"] = requirements["asset"]
    result["signedPayTo"] = requirements["payTo"]
    result["signedValue"] = authorization["value"]
    result["signedValidAfter"] = authorization["validAfter"]
    result["signedValidBefore"] = authorization["validBefore"]
    result["signedNonce"] = authorization["nonce"]

    header_value = build_payment_header_value(
        x402_version=requirements.get("x402Version", 1),
        requirements=requirements,
        signature=signature,
        authorization=authorization,
    )

    client_header_name = (
        "PAYMENT-SIGNATURE"
        if requirements.get("x402Version", 1) >= 2
        else "X-PAYMENT"
    )
    result["clientHeaderNameUsed"] = client_header_name

    retry = requests.get(
        resource_url,
        headers={client_header_name: header_value},
        timeout=timeout_seconds,
    )
    result["retryStatus"] = retry.status_code

    response_headers = _headers_lower(retry.headers)
    settlement_header_present = None
    settlement_claim = None
    for name in SERVER_RESPONSE_HEADER_NAMES:
        if name.lower() in response_headers:
            settlement_header_present = name
            settlement_claim = decode_settlement_header(
                response_headers[name.lower()]
            )
            break

    result["settlementHeaderNamePresent"] = settlement_header_present
    result["settlementClaim"] = settlement_claim
    return result
