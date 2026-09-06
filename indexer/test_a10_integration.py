"""Integration test for A10 scheduler, retry, and health."""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scheduler
import retry as retry_module

PROVIDERS = [
    {
        "provider_id": "provider-ok",
        "label": "OK Provider",
        "supported_url": "https://example.com/supported",
    },
    {
        "provider_id": "provider-retry",
        "label": "Retry Provider",
        "supported_url": "https://example.com/supported",
    },
    {
        "provider_id": "provider-perm",
        "label": "Permanent Failure Provider",
        "supported_url": "https://example.com/supported",
    },
]


def _patch_scheduler():
    """Monkeypatch network seams, returning a restore function."""
    call_log = []

    def fake_collect_and_audit(provider_id, resource_url, rpc_url, payer_private_key=None):
        call_log.append(("collect", provider_id))
        if provider_id == "provider-retry":
            call_log.append(("attempt", provider_id))
            if call_log.count(("attempt", provider_id)) < 2:
                raise ConnectionError("connection timed out")
        if provider_id == "provider-perm":
            raise ValueError("provider_id not found in registry")
        return {"outcome": "AUDITED", "auditVerdict": "CONFIRMED"}

    def fake_provider_health(provider, rpc_url=None):
        call_log.append(("health", provider.get("provider_id", "")))
        return {
            "providerId": provider.get("provider_id", ""),
            "overall": "healthy",
            "checks": {
                "supported": {"passed": True},
                "rpc": {"passed": True},
            },
        }

    def fake_sleep(seconds):
        call_log.append(("sleep", seconds))

    originals = (
        scheduler.collect_and_audit,
        scheduler.provider_health,
        scheduler.time.sleep,
    )
    scheduler.collect_and_audit = fake_collect_and_audit
    scheduler.provider_health = fake_provider_health
    scheduler.time.sleep = fake_sleep

    def restore():
        scheduler.collect_and_audit = originals[0]
        scheduler.provider_health = originals[1]
        scheduler.time.sleep = originals[2]

    return restore, call_log, fake_collect_and_audit, fake_provider_health


def run_scenario_1():
    """one_shot processes all providers and returns structured results."""
    restore, call_log, _, _ = _patch_scheduler()
    try:
        original_load = scheduler.load_providers
        scheduler.load_providers = lambda path="": PROVIDERS
        results = scheduler.one_shot()
    finally:
        scheduler.load_providers = original_load
        restore()

    print("=== Scenario 1: one_shot processes all providers ===")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    passed = True
    if len(results) != 3:
        print("FAIL: expected 3 results, got " + str(len(results)))
        passed = False
    else:
        ids = [r.get("providerId") for r in results]
        if ids != ["provider-ok", "provider-retry", "provider-perm"]:
            print("FAIL: unexpected provider order: " + str(ids))
            passed = False
        ok = next((r for r in results if r.get("providerId") == "provider-ok"), None)
        if not ok or ok.get("outcome") != "AUDITED":
            print("FAIL: provider-ok expected AUDITED, got " + str(ok))
            passed = False
        perm = next((r for r in results if r.get("providerId") == "provider-perm"), None)
        if not perm or perm.get("outcome") != "error":
            print("FAIL: provider-perm expected error, got " + str(perm))
            passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_2():
    """Retryable failure is retried and eventually succeeds."""
    restore, call_log, fake_collect, _ = _patch_scheduler()
    try:
        original_load = scheduler.load_providers
        scheduler.load_providers = lambda path="": [PROVIDERS[1]]
        results = scheduler.one_shot()
    finally:
        scheduler.load_providers = original_load
        restore()

    print("=== Scenario 2: retry recovers from transient failure ===")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    passed = True
    if len(results) != 1:
        print("FAIL: expected 1 result, got " + str(len(results)))
        passed = False
    else:
        if results[0].get("outcome") != "AUDITED":
            print("FAIL: expected AUDITED after retry, got " + str(results[0].get("outcome")))
            passed = False
        attempt_count = call_log.count(("attempt", "provider-retry"))
        if attempt_count < 2:
            print("FAIL: expected at least 2 attempts, got " + str(attempt_count))
            passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_3():
    """Permanent failure is NOT retried."""
    restore, call_log, fake_collect, _ = _patch_scheduler()
    try:
        original_load = scheduler.load_providers
        scheduler.load_providers = lambda path="": [PROVIDERS[2]]
        results = scheduler.one_shot()
    finally:
        scheduler.load_providers = original_load
        restore()

    print("=== Scenario 3: permanent failure is not retried ===")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    passed = True
    if len(results) != 1:
        print("FAIL: expected 1 result, got " + str(len(results)))
        passed = False
    else:
        if results[0].get("outcome") != "error":
            print("FAIL: expected error, got " + str(results[0].get("outcome")))
            passed = False
        if results[0].get("failureType") != "permanent":
            print("FAIL: expected failureType=permanent, got " + str(results[0].get("failureType")))
            passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def main():
    results = [
        run_scenario_1(),
        run_scenario_2(),
        run_scenario_3(),
    ]
    total = len(results)
    passed_count = sum(1 for r in results if r)
    print("")
    if passed_count == total:
        print("All " + str(total) + " scenarios passed.")
    else:
        print(str(total - passed_count) + " of " + str(total) + " scenarios FAILED.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
