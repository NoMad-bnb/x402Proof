"""Local x402 test resource server that returns 402, verifies payment, and accepts settlement."""

import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eip3009_signer import payer_address_from_key

_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_FILE)

FACILITATOR_BASE = "https://x402.org/facilitator"
REQUEST_TIMEOUT_SECONDS = 30

# Base Sepolia USDC. Same value independently confirmed twice: handoff
# section 14's field-measured constant, and the official spec's own worked
# example in specs/x402-specification-v2.md section 5.1.1, fetched live.
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
NETWORK = "eip155:84532"

# Tiny amount on purpose: 0.001 USDC per test call, so a funding round from
# the faucet covers many runs.
AMOUNT_ATOMIC = "1000"
MAX_TIMEOUT_SECONDS = 300

CLIENT_HEADER_NAMES = ["X-PAYMENT", "PAYMENT-SIGNATURE"]


def _pay_to_address() -> str:
    """payTo defaults to the same wallet that pays, read from
    PAYER_PRIVATE_KEY, so a faucet-funded testnet wallet just cycles its own
    USDC back to itself on every test call. Override with
    SELLER_PAY_TO_ADDRESS if you want a distinct recipient."""
    override = os.environ.get("SELLER_PAY_TO_ADDRESS")
    if override:
        return override
    key = os.environ.get("PAYER_PRIVATE_KEY")
    if not key:
        raise RuntimeError(
            "Set PAYER_PRIVATE_KEY (same one used by http_evidence_collector) "
            "or SELLER_PAY_TO_ADDRESS explicitly, so this server knows where "
            "to ask for payment."
        )
    return payer_address_from_key(key)


def _requirements() -> dict:
    return {
        "scheme": "exact",
        "network": NETWORK,
        "amount": AMOUNT_ATOMIC,
        "asset": USDC_BASE_SEPOLIA,
        "payTo": _pay_to_address(),
        "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
        "extra": {"name": "USDC", "version": "2"},
    }


def _payment_required_body(resource_url: str) -> bytes:
    body = {
        "x402Version": 2,
        "error": "payment required",
        "resource": {"url": resource_url, "description": "A4 test resource"},
        "accepts": [_requirements()],
        "extensions": {},
    }
    return json.dumps(body).encode("utf-8")


def _facilitator_call(path: str, payment_payload: dict, requirements: dict) -> dict:
    """Calls the real x402.org facilitator. Raises on infrastructure
    failure (network, non-200, unparseable body); returns the parsed dict
    on any response the facilitator actually gave, success or declared
    failure, since a declared failure (isValid: false, success: false) is
    information, not an exception."""
    body = json.dumps({
        "x402Version": 2,
        "paymentPayload": payment_payload,
        "paymentRequirements": requirements,
    })
    response = requests.post(
        FACILITATOR_BASE + path,
        data=body,
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise RuntimeError(
            "facilitator " + path + " returned HTTP "
            + str(response.status_code) + ": " + response.text[:500]
        )
    return response.json()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(("[seller] " + fmt) % args)

    def do_GET(self):
        if self.path != "/resource":
            self.send_response(404)
            self.end_headers()
            return

        payment_header = None
        used_name = None
        for name in CLIENT_HEADER_NAMES:
            value = self.headers.get(name)
            if value:
                payment_header = value
                used_name = name
                break

        resource_url = "http://" + self.headers.get("Host", "127.0.0.1") + "/resource"

        if not payment_header:
            body = _payment_required_body(resource_url)
            self.send_response(402)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            payment_payload = json.loads(base64.b64decode(payment_header))
        except Exception as exc:
            self._respond_error(400, "malformed_payment_header: " + str(exc))
            return

        # Never trust the client's echoed "accepted" as the yardstick.
        # Reconstruct our own requirements fresh, every time.
        requirements = _requirements()

        try:
            verify_result = _facilitator_call("/verify", payment_payload, requirements)
        except Exception as exc:
            self._respond_error(502, "verify_call_failed: " + str(exc))
            return

        if not verify_result.get("isValid"):
            self._respond_error(
                402,
                "verify_rejected: " + str(verify_result.get("invalidReason")),
            )
            return

        try:
            settle_result = _facilitator_call("/settle", payment_payload, requirements)
        except Exception as exc:
            self._respond_error(502, "settle_call_failed: " + str(exc))
            return

        if not settle_result.get("success"):
            self._respond_error(
                402,
                "settle_failed: " + str(settle_result.get("errorReason")),
            )
            return

        settlement_header_value = base64.b64encode(
            json.dumps(settle_result).encode("utf-8")
        ).decode("ascii")

        body = json.dumps({
            "message": "payment settled for real on Base Sepolia",
            "settlement": settle_result,
        }).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-PAYMENT-RESPONSE", settlement_header_value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_error(self, status: int, reason: str):
        body = json.dumps({"error": reason}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("SELLER_PORT", "8420"))
    print("Seller test resource listening on http://127.0.0.1:" + str(port) + "/resource")
    print("payTo address: " + _pay_to_address())
    print("Point http_evidence_collector.py at: http://127.0.0.1:" + str(port) + "/resource")
    server = HTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()
