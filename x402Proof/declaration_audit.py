"""GenLayer contract: independently verify the /supported declaration emitted by an x402 facilitator."""

# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json

# x402 Declaration versus Behaviour Auditor, version 2
# v2 adds signers field parsing and raises BODY_PREFIX_LIMIT to 4096.

BODY_PREFIX_LIMIT = 4096

EIP3009_SIGNATURES = [
	"transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)",
	"transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,bytes)",
]

# Legacy x402 network labels seen in the wild, mapped to their chain id.
# CAIP-2 style labels such as eip155:84532 are parsed directly instead.
NETWORK_CHAIN_IDS = {
	"base": 8453,
	"base-sepolia": 84532,
	"polygon": 137,
	"polygon-amoy": 80002,
	"avalanche": 43114,
	"avalanche-fuji": 43113,
	"arbitrum": 42161,
	"arbitrum-sepolia": 421614,
	"sei": 1329,
	"sei-testnet": 713715,
	"xlayer": 196,
	"xlayer-testnet": 1952,
}


def digest_of(text: str) -> str:
	return "0x" + Keccak256(text.encode("utf-8")).hexdigest()


def selector_hex(signature: str) -> str:
	return "0x" + Keccak256(signature.encode("utf-8")).hexdigest()[:8]


def norm_address(value: str) -> str:
	if value is None:
		return ""
	text = str(value).strip().lower()
	if text == "":
		return ""
	if not text.startswith("0x"):
		try:
			int(text, 16)
			text = "0x" + text
		except ValueError:
			pass
	return text


def hex_to_int_or_none(value: str):
	if value is None:
		return None
	text = str(value).strip()
	if text == "":
		return None
	try:
		if text.startswith("0x") or text.startswith("0X"):
			return int(text, 16)
		return int(text, 10)
	except Exception:
		return None


def chain_id_of_label(label: str):
	if label is None:
		return None
	text = str(label).strip().lower()
	if text == "":
		return None
	if text.startswith("eip155:"):
		return hex_to_int_or_none(text[7:])
	if text in NETWORK_CHAIN_IDS:
		return NETWORK_CHAIN_IDS[text]
	return None


def content_type_of(headers) -> str:
	if headers is None:
		return ""
	for key in headers:
		if str(key).lower() == "content-type":
			value = headers[key]
			if isinstance(value, bytes):
				return value.decode("utf-8", errors="replace")
			return str(value)
	return ""


def declaration_entries(parsed) -> list:
	if isinstance(parsed, list):
		return parsed
	if isinstance(parsed, dict):
		for field in ["kinds", "supported", "schemes", "data"]:
			candidate = parsed.get(field)
			if isinstance(candidate, list):
				return candidate
	return []


def extract_signer_addresses(signers_obj) -> list:
	# signers_obj is a dict mapping pattern strings to lists of address strings.
	# We extract ALL addresses from ALL patterns into a single sorted list.
	addresses = []
	if not isinstance(signers_obj, dict):
		return addresses
	for pattern, addr_list in signers_obj.items():
		if not isinstance(addr_list, list):
			continue
		for addr in addr_list:
			norm = norm_address(addr)
			if norm != "" and norm not in addresses:
				addresses.append(norm)
	addresses.sort()
	return addresses


def summarize_declaration(text: str) -> dict:
	out = {}
	try:
		parsed = json.loads(text)
	except Exception:
		out["jsonParsed"] = False
		return out

	out["jsonParsed"] = True
	entries = declaration_entries(parsed)

	pairs = []
	chain_ids = []
	unresolved = []
	facilitator_addresses = []
	fee_payers = []
	signer_addresses = []

	for item in entries:
		if not isinstance(item, dict):
			continue

		scheme = item.get("scheme")
		network = item.get("network")
		if scheme is not None or network is not None:
			pairs.append(str(scheme) + "|" + str(network))

		resolved = chain_id_of_label(network)
		if resolved is None:
			if network is not None:
				label = str(network)
				if label not in unresolved:
					unresolved.append(label)
		else:
			if resolved not in chain_ids:
				chain_ids.append(resolved)

		extra = item.get("extra")
		if isinstance(extra, dict):
			claimed = extra.get("facilitatorAddress")
			if claimed is not None:
				address = norm_address(claimed)
				if address != "" and address not in facilitator_addresses:
					facilitator_addresses.append(address)

			sponsor = extra.get("feePayer")
			if sponsor is not None:
				text_sponsor = str(sponsor).strip()
				if text_sponsor != "" and text_sponsor not in fee_payers:
					fee_payers.append(text_sponsor)

		# New in v2: extract signers field from the item itself.
		signers = item.get("signers")
		if signers is not None:
			extracted = extract_signer_addresses(signers)
			for addr in extracted:
				if addr not in signer_addresses:
					signer_addresses.append(addr)

	# Also check top level signers if present (some facilitators put it outside items)
	top_signers = parsed.get("signers")
	if top_signers is not None:
		extracted = extract_signer_addresses(top_signers)
		for addr in extracted:
			if addr not in signer_addresses:
				signer_addresses.append(addr)

	pairs.sort()
	chain_ids.sort()
	unresolved.sort()
	facilitator_addresses.sort()
	fee_payers.sort()
	signer_addresses.sort()

	out["declaredPairs"] = len(pairs)
	out["pairs"] = pairs
	out["declaredChainIds"] = chain_ids
	out["unresolvedNetworkLabels"] = unresolved
	out["declaredFacilitatorAddresses"] = facilitator_addresses
	out["declaredFeePayers"] = fee_payers
	out["declaredSignerAddresses"] = signer_addresses
	return out


def fetch_declaration(url: str) -> str:
	out = {}
	out["declarationUrl"] = url

	try:
		response = gl.nondet.web.request(url, method="GET")
	except Exception:
		out["declarationReachable"] = False
		out["declarationFailure"] = "request_failed"
		return json.dumps(out, sort_keys=True, separators=(",", ":"))

	out["declarationReachable"] = True
	out["declarationStatus"] = response.status
	out["declarationContentType"] = content_type_of(response.headers)

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
		summary = summarize_declaration(text)
		for key in summary:
			out[key] = summary[key]

	return json.dumps(out, sort_keys=True, separators=(",", ":"))


def rpc_call(rpc_url: str, method: str, params: list):
	payload = json.dumps(
		{"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
		sort_keys=True,
		separators=(",", ":"),
	)

	try:
		response = gl.nondet.web.request(
			rpc_url,
			method="POST",
			body=payload.encode("utf-8"),
			headers={"Content-Type": "application/json"},
		)
	except Exception:
		return (None, "rpc_request_failed")

	if response.status != 200:
		return (None, "rpc_status_" + str(response.status))

	raw = response.body
	if raw is None:
		return (None, "rpc_empty_body")

	try:
		parsed = json.loads(raw.decode("utf-8", errors="replace"))
	except Exception:
		return (None, "rpc_unparseable_body")

	if not isinstance(parsed, dict):
		return (None, "rpc_unexpected_shape")

	if "error" in parsed and parsed["error"] is not None:
		return (None, "rpc_error_returned")

	if "result" not in parsed:
		return (None, "rpc_no_result_field")

	return (parsed["result"], "")


def fetch_behaviour(rpc_url: str, tx_hash: str) -> str:
	out = {}
	out["rpcUrl"] = rpc_url
	out["transactionHash"] = tx_hash

	chain_result, chain_error = rpc_call(rpc_url, "eth_chainId", [])
	if chain_error != "":
		out["observedChainId"] = None
		out["behaviourFailure"] = chain_error
		return json.dumps(out, sort_keys=True, separators=(",", ":"))

	out["observedChainId"] = hex_to_int_or_none(chain_result)

	receipt, receipt_error = rpc_call(
		rpc_url, "eth_getTransactionReceipt", [tx_hash]
	)
	if receipt_error != "":
		out["behaviourFailure"] = receipt_error
		return json.dumps(out, sort_keys=True, separators=(",", ":"))

	if receipt is None:
		out["transactionFound"] = False
		return json.dumps(out, sort_keys=True, separators=(",", ":"))

	out["transactionFound"] = True
	if isinstance(receipt, dict):
		out["receiptStatus"] = str(receipt.get("status"))
		out["blockNumber"] = str(receipt.get("blockNumber"))
		logs = receipt.get("logs")
		if isinstance(logs, list):
			out["logCount"] = len(logs)

	transaction, tx_error = rpc_call(
		rpc_url, "eth_getTransactionByHash", [tx_hash]
	)
	if tx_error != "":
		out["behaviourFailure"] = tx_error
		return json.dumps(out, sort_keys=True, separators=(",", ":"))

	if not isinstance(transaction, dict):
		out["behaviourFailure"] = "rpc_unexpected_shape"
		return json.dumps(out, sort_keys=True, separators=(",", ":"))

	out["observedSender"] = norm_address(transaction.get("from"))
	out["observedTo"] = norm_address(transaction.get("to"))

	calldata = transaction.get("input")
	if calldata is None:
		out["observedSelector"] = ""
	else:
		text = str(calldata).strip().lower()
		if len(text) >= 10:
			out["observedSelector"] = text[:10]
		else:
			out["observedSelector"] = ""

	selectors = []
	for signature in EIP3009_SIGNATURES:
		selectors.append(selector_hex(signature))

	if out["observedSelector"] in selectors:
		out["observedMechanism"] = "EIP3009_TRANSFER_WITH_AUTHORIZATION"
	else:
		out["observedMechanism"] = "NOT_AN_EIP3009_AUTHORIZATION"

	return json.dumps(out, sort_keys=True, separators=(",", ":"))


def judge_attribution(record: dict) -> str:
	declared = record.get("declaredFacilitatorAddresses")
	declared_signers = record.get("declaredSignerAddresses")
	observed = record.get("observedSender")

	if observed is None or observed == "":
		return "UNDETERMINED_NO_OBSERVED_SENDER"

	# Strongest: explicit facilitatorAddress match
	if isinstance(declared, list) and len(declared) > 0 and observed in declared:
		return "ATTRIBUTION_CONFIRMED_BY_SELF_DECLARATION"

	# Weaker: match in signers list
	if isinstance(declared_signers, list) and len(declared_signers) > 0 and observed in declared_signers:
		return "ATTRIBUTION_CONFIRMED_BY_DECLARED_SIGNER_SET"

	# If no declaration at all, neutral
	if (not isinstance(declared, list) or len(declared) == 0) and (not isinstance(declared_signers, list) or len(declared_signers) == 0):
		return "NOT_DECLARED_CANNOT_ATTRIBUTE"

	# Declared but observed not found in either
	return "ATTRIBUTION_MISMATCH_DECLARED_ADDRESS_DID_NOT_SETTLE"


def judge_coverage(record: dict) -> str:
	observed_chain = record.get("observedChainId")
	declared_chains = record.get("declaredChainIds")
	unresolved = record.get("unresolvedNetworkLabels")

	if observed_chain is None:
		return "UNDETERMINED_OBSERVED_CHAIN_UNREADABLE"

	if not isinstance(declared_chains, list):
		return "UNDETERMINED_NO_DECLARATION_TO_COMPARE"

	if observed_chain in declared_chains:
		return "NETWORK_DECLARED_AND_SETTLED"

	if isinstance(unresolved, list) and len(unresolved) > 0:
		return "UNDETERMINED_UNRESOLVED_NETWORK_LABELS_PRESENT"

	return "SETTLED_ON_UNDECLARED_NETWORK"


def judge_overall(record: dict) -> str:
	if not record.get("declarationReachable", False):
		return "UNDETERMINED_DECLARATION_UNREACHABLE"

	status = record.get("declarationStatus")
	if status in [401, 402, 403]:
		return "DECLARATION_GATED_REQUIRES_CREDENTIALS"
	if status == 404:
		return "NO_SUPPORTED_ENDPOINT_PUBLISHED"
	if status != 200:
		return "UNDETERMINED_UNEXPECTED_DECLARATION_STATUS"
	if not record.get("jsonParsed", False):
		return "DECLARATION_NOT_JSON"

	if record.get("behaviourFailure") is not None:
		return "UNDETERMINED_CHAIN_SIDE_UNREADABLE"
	if not record.get("transactionFound", False):
		return "UNDETERMINED_TRANSACTION_NOT_FOUND"
	if record.get("receiptStatus") != "0x1":
		return "UNDETERMINED_TRANSACTION_NOT_SUCCESSFUL"

	attribution = record.get("attribution")
	coverage = record.get("coverage")

	if attribution == "ATTRIBUTION_MISMATCH_DECLARED_ADDRESS_DID_NOT_SETTLE":
		return "INCONSISTENT_DECLARED_OPERATOR_DID_NOT_SETTLE"
	if coverage == "SETTLED_ON_UNDECLARED_NETWORK":
		return "INCONSISTENT_SETTLED_ON_UNDECLARED_NETWORK"

	if attribution in ("ATTRIBUTION_CONFIRMED_BY_SELF_DECLARATION", "ATTRIBUTION_CONFIRMED_BY_DECLARED_SIGNER_SET") and coverage == "NETWORK_DECLARED_AND_SETTLED":
		return "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR"

	if coverage == "NETWORK_DECLARED_AND_SETTLED":
		return "PARTIAL_NETWORK_CONSISTENT_ATTRIBUTION_UNAVAILABLE"

	return "UNDETERMINED_INSUFFICIENT_OVERLAP"


class DeclarationAudit(gl.Contract):
	records: DynArray[str]

	def __init__(self) -> None:
		pass

	@gl.public.write
	def audit_declaration(
		self,
		facilitator: str,
		declaration_url: str,
		rpc_url: str,
		transaction_hash: str,
	) -> None:
		label = facilitator.strip()
		declaration_target = declaration_url.strip()
		rpc_target = rpc_url.strip()
		tx_target = transaction_hash.strip().lower()

		if declaration_target == "" or rpc_target == "" or tx_target == "":
			return

		declaration_json = gl.eq_principle.strict_eq(
			lambda: fetch_declaration(declaration_target)
		)
		behaviour_json = gl.eq_principle.strict_eq(
			lambda: fetch_behaviour(rpc_target, tx_target)
		)

		record = {}
		for key, value in json.loads(declaration_json).items():
			record[key] = value
		for key, value in json.loads(behaviour_json).items():
			record[key] = value

		record["facilitator"] = label
		record["attribution"] = judge_attribution(record)
		record["coverage"] = judge_coverage(record)
		record["verdict"] = judge_overall(record)

		self.records.append(
			json.dumps(record, sort_keys=True, separators=(",", ":"))
		)

	@gl.public.view
	def get_records(self) -> str:
		items = []
		for item in self.records:
			items.append(item)
		return json.dumps(items, separators=(",", ":"))

	@gl.public.view
	def get_record_count(self) -> str:
		return str(len(self.records))