"""Offline tests for x402_payment_client.attempt_payment GET and POST flows."""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import x402_payment_client as client

KEY = "0x" + "11" * 32
PAY_TO = "0x" + "22" * 20
PAYER = "0x" + "11" * 20
TX = "0x" + "ab" * 32

V2_402_BODY = json.dumps({
    "x402Version": 2,
    "error": "Payment required",
    "resource": {"url": "https://example.test/protected", "description": "demo", "mimeType": ""},
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:84532",
            "amount": "10000",
            "asset": "0x" + "33" * 20,
            "payTo": PAY_TO,
            "maxTimeoutSeconds": 300,
        }
    ],
})
SETTLEMENT_CLAIM = {
    "success": True,
    "transaction": TX,
    "network": "eip155:84532",
    "payer": PAYER,
}
SETTLEMENT_HEADER = base64.b64encode(json.dumps(SETTLEMENT_CLAIM).encode("utf-8")).decode("ascii")


class FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeRequests:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({
            "method": method, "url": url, "headers": headers,
            "json": json, "timeout": timeout,
        })
        return self.responses.pop(0)


def _patch_requests(fake):
    original = client.requests
    client.requests = fake
    return lambda: setattr(client, "requests", original)


def run_scenario_1():
    """GET flow: 402 -> sign -> paid GET retry -> settlement header decoded."""
    fake = FakeRequests([
        FakeResponse(402, V2_402_BODY),
        FakeResponse(200, "{}", {"X-PAYMENT-RESPONSE": SETTLEMENT_HEADER}),
    ])
    restore = _patch_requests(fake)
    try:
        result = client.attempt_payment("https://example.test/protected", KEY)
    finally:
        restore()

    print("=== Scenario 1: GET flow ===")
    passed = True
    if result.get("initialStatus") != 402 or result.get("retryStatus") != 200:
        print("FAIL: statuses wrong: " + json.dumps({k: result.get(k) for k in ("initialStatus", "retryStatus")}))
        passed = False
    if result.get("settlementClaim") != SETTLEMENT_CLAIM:
        print("FAIL: settlementClaim mismatch: " + str(result.get("settlementClaim")))
        passed = False
    if len(fake.calls) != 2:
        print("FAIL: expected 2 http calls, got " + str(len(fake.calls)))
        passed = False
    else:
        if fake.calls[0]["method"] != "GET" or fake.calls[1]["method"] != "GET":
            print("FAIL: GET flow used a different method: " + str([c["method"] for c in fake.calls]))
            passed = False
        if fake.calls[0]["json"] is not None:
            print("FAIL: GET probe sent a body")
            passed = False
    requirements = result.get("requirements") or {}
    if requirements.get("maxAmountRequired") != "10000" or requirements.get("payTo") != PAY_TO:
        print("FAIL: requirements parse wrong: " + json.dumps(requirements))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_2():
    """POST flow: body forwarded on both probe and paid retry."""
    fake = FakeRequests([
        FakeResponse(402, V2_402_BODY),
        FakeResponse(200, "{}", {"X-PAYMENT-RESPONSE": SETTLEMENT_HEADER}),
    ])
    restore = _patch_requests(fake)
    body = {"company_number": "514744887", "language": "en"}
    try:
        result = client.attempt_payment(
            "https://example.test/verify", KEY, method="POST", body=body
        )
    finally:
        restore()

    print("=== Scenario 2: POST flow forwards the body ===")
    passed = True
    if result.get("method") != "POST":
        print("FAIL: result method not POST")
        passed = False
    if len(fake.calls) != 2:
        print("FAIL: expected 2 http calls, got " + str(len(fake.calls)))
        passed = False
    else:
        for i, call in enumerate(fake.calls):
            if call["method"] != "POST":
                print("FAIL: call " + str(i) + " used method " + str(call["method"]))
                passed = False
            if call["json"] != body:
                print("FAIL: call " + str(i) + " body " + json.dumps(call["json"]))
                passed = False
    paid_call = fake.calls[1] if len(fake.calls) > 1 else {}
    if not (paid_call.get("headers") or {}).get("PAYMENT-SIGNATURE"):
        print("FAIL: paid retry did not carry PAYMENT-SIGNATURE")
        passed = False
    if result.get("settlementClaim") != SETTLEMENT_CLAIM:
        print("FAIL: settlementClaim mismatch: " + str(result.get("settlementClaim")))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_3():
    """Unsupported method raises before any request is attempted."""
    fake = FakeRequests([])
    restore = _patch_requests(fake)
    raised = False
    try:
        client.attempt_payment("https://example.test/x", KEY, method="PUT")
    except ValueError:
        raised = True
    finally:
        restore()

    print("=== Scenario 3: unsupported method refused ===")
    passed = True
    if not raised:
        print("FAIL: expected ValueError for PUT")
        passed = False
    if fake.calls:
        print("FAIL: a request was attempted: " + str(fake.calls))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_4():
    """No 402 on the probe: stop early, no signing, single request."""
    fake = FakeRequests([FakeResponse(200, "hello")])
    restore = _patch_requests(fake)
    try:
        result = client.attempt_payment("https://example.test/open", KEY)
    finally:
        restore()

    print("=== Scenario 4: no 402 -> stop early ===")
    passed = True
    if result.get("requirements") is not None:
        print("FAIL: requirements should be None, got " + str(result.get("requirements")))
        passed = False
    if len(fake.calls) != 1:
        print("FAIL: expected a single call, got " + str(len(fake.calls)))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def main():
    results = [
        run_scenario_1(),
        run_scenario_2(),
        run_scenario_3(),
        run_scenario_4(),
    ]
    total = len(results)
    passed_count = sum(1 for r in results if r)
    print("")
    if passed_count == total:
        print("All " + str(total) + " scenarios passed.")
    else:
        print(str(total - passed_count) + " of " + str(total)
              + " scenarios FAILED.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

