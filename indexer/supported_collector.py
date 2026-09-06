"""Call SupportedProbe.probe() for a facilitator and update the registry with the outcome."""

import json
import os
import sys

# Embeddable/portable Python distributions (like python-embed-amd64) do NOT
# automatically add the running script's own folder to sys.path the way a
# normal Python install does. Without this, importing sibling modules in
# this same folder (genlayer_connection, contracts_config,
# provider_registry) fails with ModuleNotFoundError even though the files
# are right there. This line makes it work the same way regardless of
# which kind of Python is running it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genlayer_connection import get_client
from contract_callers import submit_supported_probe
from contracts_config import SUPPORTED_PROBE_ADDRESS
from provider_registry import update_status, get_provider


def run_probe(facilitator: str, url: str, wait_status: str = "ACCEPTED",
              wait_interval: int = 5000, wait_retries: int = 60,
              capture: dict | None = None) -> dict:
    """Call SupportedProbe.probe(facilitator, url), wait for the result,
    then read back the matching entry from get_probes().

    Returns the parsed probe record (a dict) for the just-submitted probe.
    Raises on any failure; does not return a partial or guessed result.

    wait_status defaults to "ACCEPTED" because ACCEPTED is reached first
    and is sufficient here: SupportedProbe never revises a record after
    writing it, so there is nothing further to wait for once it is
    accepted.

    wait_interval/wait_retries override the SDK's default 10x3000ms = 30s
    wait window. The defaults here (60 x 5000ms = 300s) give consensus
    rotation phases room to settle without changing any behaviour on the
    fast path.

    MIGRATION (post-A8): the write/wait/read mechanics used to be this
    file's own inline copy of the pattern. They now delegate to
    contract_callers.submit_supported_probe(), the single shared
    implementation, which additionally refuses an empty url BEFORE any
    gas. The client is still built HERE, through this module's own
    get_client() seam, and passed in explicitly.
    """
    client = get_client()

    return submit_supported_probe(
        facilitator=facilitator,
        url=url,
        client=client,
        wait_status=wait_status,
        wait_interval=wait_interval,
        wait_retries=wait_retries,
        capture=capture,
    )


def probe_and_update_registry(provider_id: str, capture: dict | None = None) -> dict:
    """Convenience wrapper for the common case: look up a provider already
    in the registry, probe its supported_url, and write the outcome back.

    Raises ValueError if the provider is not in the registry or has no
    supported_url on file, rather than guessing a URL.

    ATTRIBUTION GUARD: a probe whose consensus ends UNDETERMINED /
    MAJORITY_DISAGREE appends NOTHING to the contract, but the
    append-then-read pattern still reads back the PREVIOUS caller's
    record, and the ACCEPTED wait passes because UNDETERMINED is a
    decided state. Without a guard, the previous record's outcome gets
    attributed to this provider. The guard reads the probe count before
    and after the write: our record exists only if the count grew, and it
    is provably ours only if its facilitator and url match this provider.
    Anything else refuses - the registry is never updated from a record we
    cannot prove is ours.
    """
    provider = get_provider(provider_id)
    if provider is None:
        raise ValueError("provider_id not found in registry: " + provider_id)

    if not provider.get("supported_url"):
        raise ValueError(
            "provider "
            + provider_id
            + " has no supported_url on file, cannot probe"
        )

    client = get_client()
    count_before = _read_probe_count(client)

    result = run_probe(provider_id, provider["supported_url"], capture=capture)

    count_after = _read_probe_count(client)
    if count_after <= count_before:
        raise RuntimeError(
            "PROBE_NO_RECORD_APPENDED: probe count before="
            + str(count_before)
            + " after="
            + str(count_after)
            + " for provider '"
            + provider_id
            + "'. The consensus did not accept a write (UNDETERMINED or "
            "disagreement), so no record belongs to this probe. The "
            "record that would have been read back belongs to a previous "
            "probe and is refused here. Registry left untouched."
        )
    if (
        result.get("facilitator") != provider_id
        or result.get("url") != provider["supported_url"]
    ):
        raise RuntimeError(
            "PROBE_RECORD_MISMATCH: newest record carries facilitator='"
            + str(result.get("facilitator"))
            + "' url='"
            + str(result.get("url"))
            + "' but this probe was for facilitator='"
            + provider_id
            + "' url='"
            + str(provider["supported_url"])
            + "'. Refusing to attribute it. Registry left untouched."
        )

    outcome = result.get("outcome", "UNKNOWN_OUTCOME_FIELD_MISSING")
    update_status(provider_id, outcome)
    return result


def _read_probe_count(client) -> int:
    """Read the contract's probe count as an int (a free view call)."""
    raw = client.read_contract(
        address=SUPPORTED_PROBE_ADDRESS,
        function_name="get_probe_count",
        args=[],
    )
    return int(str(raw))


if __name__ == "__main__":
    # Manual smoke test, meant to be run by a human with GENLAYER_PRIVATE_KEY
    # already set, against one already-known-good provider. Not run
    # automatically as part of any pipeline.
    import sys

    if len(sys.argv) != 2:
        print("Usage: python3 supported_collector.py <provider_id>")
        print("Example: python3 supported_collector.py x402.mikedotexe.com")
        sys.exit(1)

    provider_id_arg = sys.argv[1]
    print("Probing " + provider_id_arg + " ...")
    outcome_record = probe_and_update_registry(provider_id_arg)
    print(json.dumps(outcome_record, indent=2, ensure_ascii=False))