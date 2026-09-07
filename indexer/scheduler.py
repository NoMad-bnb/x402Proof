"""Main automation loop that cycles providers with health checks, retry, and per-provider timeouts."""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from provider_registry import load_registry, get_provider
from http_evidence_collector import collect_and_audit
from health import provider_health
from retry import retry, RetryConfig, classify_failure

PROVIDERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json")

CYCLE_INTERVAL_SECONDS = 300
PROVIDER_TIMEOUT_SECONDS = 120

RETRY_CONFIG = RetryConfig(
    max_attempts=2,
    initial_delay_seconds=5,
    backoff_multiplier=2.0,
    max_delay_seconds=30,
    jitter_seconds=2,
)


def load_providers(path: str = PROVIDERS_PATH) -> list:
    """Load the provider registry. Raises on corrupt/missing file."""
    return load_registry(path)


def _run_provider(provider: dict) -> dict:
    """Execute one provider's full pipeline."""
    provider_id = provider.get("provider_id", "")
    started = datetime.now(timezone.utc).isoformat()
    result = {
        "providerId": provider_id,
        "startedAt": started,
        "finishedAt": None,
        "outcome": "unknown",
        "error": None,
    }

    try:
        health = provider_health(provider)
        if health["overall"] == "unhealthy":
            result["outcome"] = "skipped_unhealthy"
            result["error"] = "provider unreachable: supported=" + str(health["checks"]["supported"]["passed"]) + " rpc=" + str(health["checks"]["rpc"]["passed"])
            return result

        summary = _collect_with_retry(provider_id)
        result["outcome"] = summary.get("outcome", "unknown")
        result["summary"] = summary
    except Exception as exc:
        result["outcome"] = "error"
        result["error"] = str(exc)
        failure_type = classify_failure(exc)
        result["failureType"] = failure_type

    result["finishedAt"] = datetime.now(timezone.utc).isoformat()
    return result


@retry(config=RETRY_CONFIG)
def _collect_with_retry(provider_id: str) -> dict:
    """Collect and audit for one provider, with retry on transient failures."""
    return collect_and_audit(
        provider_id=provider_id,
        resource_url="http://127.0.0.1:8420/resource",
        rpc_url="https://sepolia.base.org",
    )


def run_cycle() -> list:
    """Run one full cycle over all providers in the registry."""
    providers = load_providers()
    results = []
    for provider in providers:
        try:
            result = _run_provider(provider)
        except Exception as exc:
            result = {
                "providerId": provider.get("provider_id", ""),
                "startedAt": datetime.now(timezone.utc).isoformat(),
                "finishedAt": datetime.now(timezone.utc).isoformat(),
                "outcome": "error",
                "error": str(exc),
                "failureType": classify_failure(exc),
            }
        results.append(result)
    return results


def run_scheduler(
    cycle_interval_seconds: int = CYCLE_INTERVAL_SECONDS,
    max_cycles: int = 0,
) -> None:
    """Run the scheduler loop.

    Args:
        cycle_interval_seconds: seconds to sleep between cycles.
        max_cycles: 0 means run forever; positive integer means stop
            after that many cycles.
    """
    cycle = 0
    api_url = os.environ.get("X402_API_URL", "")
    if not api_url:
        print("WARNING: X402_API_URL is not set. Data will NOT be pushed to API.")
    else:
        print("API push enabled: " + api_url)
    print("Scheduler started. interval=" + str(cycle_interval_seconds) + "s max_cycles=" + str(max_cycles if max_cycles > 0 else "infinite"))

    try:
        while True:
            cycle = cycle + 1
            started = datetime.now(timezone.utc).isoformat()
            print("")
            print("=== Cycle " + str(cycle) + " started at " + started + " ===")

            results = run_cycle()
            for result in results:
                status = result.get("outcome", "unknown")
                detail = result.get("error") or result.get("summary", {}).get("auditVerdict", "")
                print("  " + result.get("providerId", "") + ": " + status + (" (" + detail + ")" if detail else ""))

            finished = datetime.now(timezone.utc).isoformat()
            print("Cycle " + str(cycle) + " finished at " + finished)

            if max_cycles > 0 and cycle >= max_cycles:
                print("Reached max_cycles=" + str(max_cycles) + ". Stopping.")
                break

            print("Sleeping " + str(cycle_interval_seconds) + "s until next cycle...")
            time.sleep(cycle_interval_seconds)

    except KeyboardInterrupt:
        print("")
        print("Scheduler stopped by user.")


def one_shot() -> list:
    """Run exactly one cycle and return the results. Useful for cron."""
    return run_cycle()


def _run_self_test() -> None:
    """Offline checks for scheduler logic that does not need live network."""
    checks = []

    # 1. load_providers reads the real file.
    providers = load_providers()
    checks.append((
        "load_providers reads providers.json",
        isinstance(providers, list) and len(providers) > 0,
    ))

    # 2. classify_failure in scheduler context.
    checks.append((
        "classify_failure available and returns expected categories",
        classify_failure(ConnectionError("timeout")) == "retryable"
        and classify_failure(ValueError("provider_id not found")) == "permanent",
    ))

    # 3. RetryConfig is reusable.
    config = RetryConfig(max_attempts=1, initial_delay_seconds=0.01, jitter_seconds=0)
    checks.append((
        "RetryConfig accepts valid values",
        config.max_attempts == 1 and config.initial_delay_seconds == 0.01,
    ))

    # 4. RetryConfig rejects invalid values.
    raised = False
    try:
        RetryConfig(max_attempts=0)
    except ValueError:
        raised = True
    checks.append((
        "RetryConfig rejects max_attempts=0",
        raised,
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
    import argparse

    parser = argparse.ArgumentParser(description="A10 Scheduler")
    parser.add_argument("--self-test", action="store_true", help="Run self-test and exit")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--interval", type=int, default=CYCLE_INTERVAL_SECONDS, help="Seconds between cycles")
    parser.add_argument("--max-cycles", type=int, default=0, help="Stop after N cycles (0 = infinite)")
    args = parser.parse_args()

    if args.self_test:
        _run_self_test()
    elif args.once:
        results = one_shot()
        for result in results:
            print(json.dumps(result, ensure_ascii=False))
    else:
        run_scheduler(
            cycle_interval_seconds=args.interval,
            max_cycles=args.max_cycles,
        )
