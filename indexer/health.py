"""Lightweight health checks for facilitator /supported and JSON-RPC eth_chainId endpoints."""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from contracts_config import EXPECTED_CHAIN_ID

REQUEST_TIMEOUT_SECONDS = 10


class HealthRecord:
    """One health-check result."""

    def __init__(self, target: str, check_type: str, passed: bool,
                 message: str = "", checked_at: str = None):
        self.target = target
        self.check_type = check_type
        self.passed = passed
        self.message = message
        self.checked_at = checked_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "checkType": self.check_type,
            "passed": self.passed,
            "message": self.message,
            "checkedAt": self.checked_at,
        }


def check_rpc(rpc_url: str) -> HealthRecord:
    """Check one JSON-RPC endpoint with the lightest possible call:
    eth_chainId. This verifies reachability and basic JSON-RPC shape
    without fetching block data."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []
    })
    try:
        response = requests.post(
            rpc_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        parsed = response.json()
        if "error" in parsed and parsed["error"] is not None:
            return HealthRecord(
                target=rpc_url,
                check_type="rpc",
                passed=False,
                message="rpc_error: " + json.dumps(parsed["error"]),
            )
        if "result" not in parsed:
            return HealthRecord(
                target=rpc_url,
                check_type="rpc",
                passed=False,
                message="rpc_no_result_field",
            )
        return HealthRecord(
            target=rpc_url,
            check_type="rpc",
            passed=True,
            message="chain_id=" + str(parsed["result"]),
        )
    except Exception as exc:
        return HealthRecord(
            target=rpc_url,
            check_type="rpc",
            passed=False,
            message=str(exc),
        )


def check_supported(supported_url: str) -> HealthRecord:
    """Check one /supported endpoint. HTTP 200 with any body is
    considered healthy; the contract's classify() decides the semantic
    meaning of the response. Non-200 or connection failure is unhealthy."""
    try:
        response = requests.get(
            supported_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "")
            return HealthRecord(
                target=supported_url,
                check_type="supported",
                passed=True,
                message="status=200 content_type=" + content_type,
            )
        return HealthRecord(
            target=supported_url,
            check_type="supported",
            passed=False,
            message="http_status=" + str(response.status_code),
        )
    except Exception as exc:
        return HealthRecord(
            target=supported_url,
            check_type="supported",
            passed=False,
            message=str(exc),
        )


def provider_health(provider: dict, rpc_url: str = None) -> dict:
    """Run the two health checks relevant to one provider: its
    /supported endpoint and the configured RPC node. Returns a dict
    with both results and an overall status."""
    supported_url = provider.get("supported_url")
    results = {}

    if supported_url:
        results["supported"] = check_supported(supported_url).to_dict()
    else:
        results["supported"] = HealthRecord(
            target="",
            check_type="supported",
            passed=False,
            message="no supported_url configured",
        ).to_dict()

    effective_rpc = rpc_url or "https://sepolia.base.org"
    results["rpc"] = check_rpc(effective_rpc).to_dict()

    overall = "healthy"
    if not results["supported"]["passed"] and not results["rpc"]["passed"]:
        overall = "unhealthy"
    elif not results["supported"]["passed"] or not results["rpc"]["passed"]:
        overall = "degraded"

    return {
        "providerId": provider.get("provider_id", ""),
        "overall": overall,
        "checks": results,
    }


def _run_self_test() -> None:
    """Offline checks for the logic that does not need live HTTP.
    The live HTTP paths are exercised by running the scheduler against
    a real provider; this session cannot reach arbitrary HTTPS hosts."""
    checks = []

    # 1. check_supported returns passed=True for 200.
    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        def json(self):
            return {}
        def raise_for_status(self):
            pass

    class FakeGet:
        def __init__(self, response):
            self._response = response
        def get(self, url, timeout=None):
            return self._response

    import health as health_module
    original_get = requests.get
    requests.get = lambda url, timeout=None: FakeResponse()
    try:
        record = health_module.check_supported("https://example.com/supported").to_dict()
        checks.append((
            "check_supported marks 200 as healthy",
            record["passed"] is True and record["checkType"] == "supported",
        ))
    finally:
        requests.get = original_get

    # 2. check_supported returns passed=False for 404.
    class FakeResponse404:
        status_code = 404
        headers = {"Content-Type": "text/html"}
        def json(self):
            return {}
        def raise_for_status(self):
            raise Exception("HTTP 404")

    requests.get = lambda url, timeout=None: FakeResponse404()
    try:
        record = health_module.check_supported("https://example.com/supported").to_dict()
        checks.append((
            "check_supported marks 404 as unhealthy",
            record["passed"] is False and "404" in record["message"],
        ))
    finally:
        requests.get = original_get

    # 3. check_supported returns passed=False on connection failure.
    def raising_get(url, timeout=None):
        raise ConnectionError("connection timed out")

    requests.get = raising_get
    try:
        record = health_module.check_supported("https://example.com/supported").to_dict()
        checks.append((
            "check_supported marks connection failure as unhealthy",
            record["passed"] is False,
        ))
    finally:
        requests.get = original_get

    # 4. provider_health returns overall=healthy when both pass.
    provider_good = {"provider_id": "test", "supported_url": "https://example.com/supported"}

    class FakeResponseOK:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        def json(self):
            return {"jsonrpc": "2.0", "id": 1, "result": "0x14a34"}
        def raise_for_status(self):
            pass

    requests.get = lambda url, timeout=None: FakeResponseOK()
    requests.post = lambda url, data=None, headers=None, timeout=None: FakeResponseOK()
    try:
        result = health_module.provider_health(provider_good, rpc_url="https://example.com/rpc")
        checks.append((
            "provider_health returns overall=healthy when both pass",
            result["overall"] == "healthy"
            and result["checks"]["supported"]["passed"] is True
            and result["checks"]["rpc"]["passed"] is True,
        ))
    finally:
        pass

    # 5. provider_health returns overall=unhealthy when both fail.
    requests.get = raising_get
    requests.post = raising_get
    try:
        result = health_module.provider_health(provider_good, rpc_url="https://example.com/rpc")
        checks.append((
            "provider_health returns overall=unhealthy when both fail",
            result["overall"] == "unhealthy",
        ))
    finally:
        pass

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
