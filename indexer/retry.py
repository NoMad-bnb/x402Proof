"""Exponential-backoff retry helper that classifies failures as retryable, permanent, or fatal."""

import random
import time


class RetryConfig:
    """Parameters for one retry policy."""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay_seconds: float = 1.0,
        backoff_multiplier: float = 2.0,
        max_delay_seconds: float = 60.0,
        jitter_seconds: float = 1.0,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if initial_delay_seconds <= 0:
            raise ValueError("initial_delay_seconds must be > 0")
        if backoff_multiplier <= 0:
            raise ValueError("backoff_multiplier must be > 0")
        if max_delay_seconds <= 0:
            raise ValueError("max_delay_seconds must be > 0")
        if jitter_seconds < 0:
            raise ValueError("jitter_seconds must be >= 0")

        self.max_attempts = max_attempts
        self.initial_delay_seconds = initial_delay_seconds
        self.backoff_multiplier = backoff_multiplier
        self.max_delay_seconds = max_delay_seconds
        self.jitter_seconds = jitter_seconds


# Default policy: 3 attempts, 1s base, 2x backoff, 60s cap, 1s jitter.
DEFAULT_RETRY_CONFIG = RetryConfig()


def _is_retryable(exc: Exception) -> bool:
    """Decide whether one exception is retryable. Retryable means:
    transient infrastructure failure that is likely to clear if waited
    on. Permanent failures must NOT be retried."""
    return classify_failure(exc) == "retryable"


def classify_failure(exc: Exception) -> str:
    """Map one exception to one of:
        retryable    transient, likely to clear
        permanent    deterministic failure, retrying will not help
        fatal        corrupted state, stop everything

    The mapping is intentionally conservative: when in doubt, mark
    permanent rather than retryable, because silent retries are
    forbidden by the project's standing rules."""
    message = str(exc).lower()

    if isinstance(exc, KeyboardInterrupt):
        return "fatal"

    # Connection-level failures are retryable.
    if any(keyword in message for keyword in (
        "connection", "timed out", "timeout", "temporary failure",
        "name resolution", "socket", "broken pipe",
    )):
        return "retryable"

    # HTTP 429 / 503 are explicitly retryable.
    if any(keyword in message for keyword in (
        "429", "503", "rate limit", "service unavailable",
    )):
        return "retryable"

    # Malformed input, missing required field, bad configuration: permanent.
    if any(keyword in message for keyword in (
        "not found in registry", "provider_id not found",
        "invalid", "malformed", "not a dict", "must be",
        "empty", "missing",
    )):
        return "permanent"

    # GenLayer contract errors after write are permanent/fatal: gas is
    # already spent, retrying costs more and can double-record.
    if any(keyword in message for keyword in (
        "contract", "consensus", "vm_error", "invalid_contract",
    )):
        return "permanent"

    # Default: treat unknowns as permanent rather than retryable, to
    # honor the standing rule that failure must raise loudly.
    return "permanent"


def retry(config: RetryConfig = None, on: type = None):
    """Retry a callable with exponential backoff + jitter.

    Args:
        config: RetryConfig instance. Defaults to DEFAULT_RETRY_CONFIG.
        on: optional exception class. When provided, only exceptions
            of this type (or subclass) are retried. When None,
            _is_retryable() decides per-exception.

    Returns:
        Decorator or context manager depending on usage.

    Usage as decorator:
        @retry(config=RetryConfig(max_attempts=5))
        def call_something(): ...

    Usage as helper:
        retry(config=my_config)(callable)(*args, **kwargs)
    """
    if config is None:
        config = DEFAULT_RETRY_CONFIG

    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exc = None
            delay = config.initial_delay_seconds

            for attempt in range(1, config.max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc

                    if attempt >= config.max_attempts:
                        break

                    if on is not None and not isinstance(exc, on):
                        break

                    if not _is_retryable(exc):
                        break

                    jitter = random.uniform(0, config.jitter_seconds)
                    sleep_time = min(delay + jitter, config.max_delay_seconds)
                    time.sleep(sleep_time)
                    delay = min(delay * config.backoff_multiplier, config.max_delay_seconds)

            raise last_exc

        return wrapper

    return decorator


def _run_self_test() -> None:
    """Offline checks proving this file does what its docstring claims."""
    checks = []

    # 1. retry succeeds on the first attempt when no exception is raised.
    call_count = 0

    @retry(config=RetryConfig(max_attempts=3, initial_delay_seconds=0.01, jitter_seconds=0))
    def succeeds_immediately():
        nonlocal call_count
        call_count = call_count + 1
        return "ok"

    checks.append((
        "retry returns on first success without sleeping",
        succeeds_immediately() == "ok" and call_count == 1,
    ))

    # 2. retry retries a retryable exception and eventually succeeds.
    call_count = 0

    @retry(config=RetryConfig(max_attempts=3, initial_delay_seconds=0.01, jitter_seconds=0))
    def fails_twice_then_works():
        nonlocal call_count
        call_count = call_count + 1
        if call_count < 3:
            raise ConnectionError("connection timed out")
        return "recovered"

    checks.append((
        "retry recovers after transient failures",
        fails_twice_then_works() == "recovered" and call_count == 3,
    ))

    # 3. retry stops on permanent exception.
    call_count = 0

    @retry(config=RetryConfig(max_attempts=5, initial_delay_seconds=0.01, jitter_seconds=0))
    def permanent_failure():
        nonlocal call_count
        call_count = call_count + 1
        raise ValueError("provider_id not found in registry")

    raised = False
    try:
        permanent_failure()
    except ValueError:
        raised = True
    checks.append((
        "retry stops on permanent failure after exactly one attempt",
        raised and call_count == 1,
    ))

    # 4. retry exhausts max_attempts on retryable failures.
    call_count = 0

    @retry(config=RetryConfig(max_attempts=2, initial_delay_seconds=0.01, jitter_seconds=0))
    def always_fails():
        nonlocal call_count
        call_count = call_count + 1
        raise TimeoutError("timed out")

    raised_timeout = False
    try:
        always_fails()
    except TimeoutError:
        raised_timeout = True
    checks.append((
        "retry raises after exhausting max_attempts on retryable failure",
        raised_timeout and call_count == 2,
    ))

    # 5. classify_failure maps connection errors to retryable.
    checks.append((
        "classify_failure marks connection error retryable",
        classify_failure(ConnectionError("connection timed out")) == "retryable",
    ))

    # 6. classify_failure maps registry miss to permanent.
    checks.append((
        "classify_failure marks provider not found permanent",
        classify_failure(ValueError("provider_id not found in registry")) == "permanent",
    ))

    # 7. classify_failure maps KeyboardInterrupt to fatal.
    checks.append((
        "classify_failure marks KeyboardInterrupt fatal",
        classify_failure(KeyboardInterrupt()) == "fatal",
    ))

    # 8. RetryConfig rejects invalid max_attempts.
    raised_invalid = False
    try:
        RetryConfig(max_attempts=0)
    except ValueError:
        raised_invalid = True
    checks.append((
        "RetryConfig rejects max_attempts < 1",
        raised_invalid,
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
