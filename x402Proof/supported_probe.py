"""GenLayer contract: actively probe an x402 facilitator's /supported endpoint under consensus."""

# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json

# x402 Facilitator Active Probe, version 1
# Probes /supported endpoints under GenLayer consensus for independent verification.
#      MEASUREMENT and a legitimate published finding about the operator.
#
#   3. MAJORITY_DISAGREE.
#      The response is not byte stable across validators, or egress differs.
#      Then strict_eq is the wrong tool for this endpoint and we must move to
#      a normalized projection instead of the raw body.
#
#   4. The request path is unavailable inside the runtime.
#      Then active probing must live outside the contract and re enter as
#      signed evidence, which is a very different and much weaker design.
#
# We do not know which of the four we will get. That is the point of running it.
#
# DETERMINISM RULES OBEYED HERE
#
# - Everything inside the nondet block is a MODULE LEVEL function taking
#   explicit plain arguments. No self, no storage, no contract state.
# - No clocks, no randomness, no block height reads.
# - Exceptions are collapsed into a fixed token. The exception TEXT is never
#   recorded, because two validators can fail with different wording and that
#   would manufacture a disagreement out of an agreement.
# - Only the content-type header is kept. Date and request-id headers differ
#   per validator by design and would guarantee a false disagreement.
# - Serialization is always sorted with fixed separators.
#
# HONEST LIMITS OF THIS PROBE
#
# - A passing probe proves what the facilitator DECLARED, not what it DOES.
#   Comparing declaration against on chain behaviour is a later layer.
# - One probe is one moment. An operator can answer honestly today and change
#   tomorrow. Only a series has meaning, which is why results accumulate.
# - The body prefix is capped. A truncated body is marked, never silently cut.
# ---------------------------------------------------------------------------

BODY_PREFIX_LIMIT = 1200


def digest_of(text: str) -> str:
	# A fresh Keccak256 instance per call. Measured earlier: .update() and
	# bytes() are unusable in this runtime, only .hexdigest() is reliable.
	return "0x" + Keccak256(text.encode("utf-8")).hexdigest()


def content_type_of(headers) -> str:
	# headers is dict[str, bytes]. Header names are compared lowercase because
	# HTTP header casing is not guaranteed to be stable between servers.
	if headers is None:
		return ""
	for key in headers:
		if str(key).lower() == "content-type":
			value = headers[key]
			if isinstance(value, bytes):
				return value.decode("utf-8", errors="replace")
			return str(value)
	return ""


def summarize_json(text: str) -> dict:
	# A best effort structural read of the declaration. Failure to parse is a
	# recorded fact, not an error, because a facilitator returning HTML instead
	# of JSON is itself a finding worth publishing.
	out = {}
	try:
		parsed = json.loads(text)
	except Exception:
		out["jsonParsed"] = False
		return out

	out["jsonParsed"] = True

	entries = None
	if isinstance(parsed, list):
		entries = parsed
	elif isinstance(parsed, dict):
		for field in ["kinds", "supported", "schemes", "data"]:
			candidate = parsed.get(field)
			if isinstance(candidate, list):
				entries = candidate
				out["listField"] = field
				break

	if entries is None:
		out["declaredPairs"] = 0
		return out

	pairs = []
	for item in entries:
		if not isinstance(item, dict):
			continue
		scheme = item.get("scheme")
		network = item.get("network")
		if scheme is None and network is None:
			continue
		pairs.append(str(scheme) + "|" + str(network))

	# Sorted so that a server reordering its own list does not read as a change.
	pairs.sort()
	out["declaredPairs"] = len(pairs)
	out["pairs"] = pairs
	return out


def fetch_supported(url: str) -> str:
	out = {}
	out["url"] = url

	try:
		response = gl.nondet.web.request(url, method="GET")
	except Exception:
		# Deliberately no exception text. See the determinism rules above.
		out["reachable"] = False
		out["failure"] = "request_failed"
		return json.dumps(out, sort_keys=True, separators=(",", ":"))

	out["reachable"] = True
	out["status"] = response.status
	out["contentType"] = content_type_of(response.headers)

	raw = response.body
	if raw is None:
		text = ""
	else:
		text = raw.decode("utf-8", errors="replace")

	out["bodyLength"] = len(text)
	out["bodyDigest"] = digest_of(text)
	out["bodyTruncated"] = len(text) > BODY_PREFIX_LIMIT
	out["bodyPrefix"] = text[:BODY_PREFIX_LIMIT]

	if response.status == 200:
		summary = summarize_json(text)
		for key in summary:
			out[key] = summary[key]

	return json.dumps(out, sort_keys=True, separators=(",", ":"))


def classify(record: dict) -> str:
	if not record.get("reachable", False):
		return "UNREACHABLE_FROM_VALIDATORS"

	status = record.get("status")

	if status == 200:
		if not record.get("jsonParsed", False):
			return "RESPONDED_BUT_NOT_JSON"
		if record.get("declaredPairs", 0) == 0:
			return "RESPONDED_JSON_BUT_NO_DECLARED_PAIRS"
		return "DECLARATION_CAPTURED"

	if status in [401, 402, 403]:
		return "GATED_REQUIRES_CREDENTIALS"
	if status == 404:
		return "NO_SUPPORTED_ENDPOINT"
	if status == 429:
		return "RATE_LIMITED"
	if isinstance(status, int) and status >= 500:
		return "SERVER_ERROR"

	return "UNEXPECTED_STATUS"


class SupportedProbe(gl.Contract):
	probes: DynArray[str]

	def __init__(self) -> None:
		# Required. A contract with no explicit __init__ fails at schema time.
		pass

	@gl.public.write
	def probe(self, facilitator: str, url: str) -> None:
		label = facilitator.strip()
		target = url.strip()

		if target == "":
			return

		evidence_json = gl.eq_principle.strict_eq(
			lambda: fetch_supported(target)
		)

		record = json.loads(evidence_json)
		record["facilitator"] = label
		record["outcome"] = classify(record)

		self.probes.append(
			json.dumps(record, sort_keys=True, separators=(",", ":"))
		)

	@gl.public.view
	def get_probes(self) -> str:
		items = []
		for item in self.probes:
			items.append(item)
		return json.dumps(items, separators=(",", ":"))

	@gl.public.view
	def get_probe_count(self) -> str:
		return str(len(self.probes))
