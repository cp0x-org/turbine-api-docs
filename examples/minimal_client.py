#!/usr/bin/env python3
"""Minimal Turbine API client for Ethereum mainnet.

A single-file Python reference showing how to:
  * check /status and fetch /config with pin verification
  * SIWE authenticate
  * read Permit2 nonce on-chain
  * build and EIP-712 sign a Permit2 PermitSingle
  * build a spreadCurve (flat "constant" curve and a 4-knot "auto" curve)
  * submit an /add_order with the correct yParity convention
  * poll /order_states
  * cancel an order

Dependencies:  pip install requests eth-account web3

Usage:
    export WALLET_PRIVATE_KEY=0x...
    python minimal_client.py

License: CC-BY-4.0. Keep the attribution header if you reuse.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address
from web3 import Web3


# ----------------------------------------------------------------------
# Configuration (production Ethereum mainnet, API v0.114.1)
# ----------------------------------------------------------------------

API_URL = "https://api.turbine.exchange/api"
RPC_URL = "https://rpc.mevblocker.io"
CHAIN_ID = 1

# Pin against these values — /api/config must match on every startup.
# /api/config exposes "version": "0.114.1" and the full token list (~401 tokens,
# each with CEX oracle mappings); the addresses below are the v0.114.1 deployment.
PIN_SETTLER = "0xbb3e81c0563dc61719696475f5c7b5e011a73f8a"
PIN_SIGNER = "0x89c740fea6bd1df86d0f8dff3f4c4c23cb598890"
PIN_LP_HOOK = "0xa44ff524f78858e015fcca322cb7d16aeb89a088"
PIN_LP_ROUTER = "0x8e7cc22eda4e2d3a8275fd88cf061681b42ce3d1"
PIN_POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
PIN_SIWE_DOMAIN = "app.turbine.exchange"
PIN_SIWE_URI = "https://api.turbine.exchange/api"

PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

NULL_ADDRESS = "0x0000000000000000000000000000000000000000"
MAX_UINT160 = (1 << 160) - 1

# SpreadCurve domain (from turbine-sdk/src/constants.ts).
# deltaBps is signed, 1 unit = 1 bp = 0.01%. Negative = maker better than mid.
MIN_DELTA_BPS = -10000
MAX_DELTA_BPS = 9999
# windowBps = normalized order-window time: 0 = startTime, 10000 = endTime.
# Interior knots must be in [MIN_WINDOW_BPS, MAX_WINDOW_BPS].
MIN_WINDOW_BPS = 1
MAX_WINDOW_BPS = 9999
# SDK-side DoS guard; the backend enforces a tighter bound by duration/block interval.
MAX_SPREAD_CURVE_POINTS = 1024

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("turbine")


# ----------------------------------------------------------------------
# Data shapes
# ----------------------------------------------------------------------


@dataclass
class StructuredSignature:
    r: str
    s: str
    y_parity: bool
    v: str

    def to_permit2_json(self) -> dict[str, Any]:
        # /api/add_order expects yParity as a JSON bool
        return {"r": self.r, "s": self.s, "yParity": self.y_parity, "v": self.v}

    def to_siwe_json(self) -> dict[str, Any]:
        # /api/verify expects yParity as a hex string
        return {
            "r": self.r,
            "s": self.s,
            "yParity": "0x1" if self.y_parity else "0x0",
            "v": self.v,
        }


@dataclass
class OrderIntent:
    owner: str
    sell_token: str
    buy_token: str
    sell_amount: int
    min_buy_amount: int
    # v0.114: the scalar "midPriceDelta" field is gone. Orders carry a
    # "spreadCurve" — a signed delta curve over the order's time window.
    # Build one with spread_constant(...) or spread_auto(...).
    spread_curve: dict[str, Any]
    start_time: int
    end_time: int
    salt: str
    partial_fill: bool = True
    call_data: str = "0x"
    call_data_target: str = NULL_ADDRESS

    def to_json(self) -> dict[str, Any]:
        return {
            "owner": to_checksum_address(self.owner),
            "sellToken": to_checksum_address(self.sell_token),
            "buyToken": to_checksum_address(self.buy_token),
            "sellAmount": str(self.sell_amount),
            "minBuyAmount": str(self.min_buy_amount),
            "spreadCurve": self.spread_curve,
            "startTime": str(self.start_time),
            "endTime": str(self.end_time),
            "partialFill": bool(self.partial_fill),
            "callData": self.call_data,
            "callDataTarget": to_checksum_address(self.call_data_target),
            "salt": self.salt,
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def random_salt() -> str:
    return "0x" + secrets.token_bytes(32).hex()


def _check_delta_in_domain(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an int, got {value!r}")
    if value < MIN_DELTA_BPS or value > MAX_DELTA_BPS:
        raise ValueError(
            f"{name} must be in [{MIN_DELTA_BPS}, {MAX_DELTA_BPS}], got {value}"
        )


def spread_constant(delta_bps: int) -> dict[str, Any]:
    """Flat spread curve: the same delta across the whole order window.

    Mirrors turbine-sdk `constant(deltaBps)`. This reproduces the old fixed
    `midPriceDelta` behavior. Negative delta = maker price better than mid.
    """
    _check_delta_in_domain(delta_bps, "delta_bps")
    return {
        "startDeltaBps": delta_bps,
        "endDeltaBps": delta_bps,
        "points": [],
    }


def spread_auto(
    fast_spread_bps: int,
    delta_bps: Optional[int] = None,
    yolo_bps: int = -1000,
) -> dict[str, Any]:
    """Four-knot "auto" spread curve (the v0.114 auto-spread order type).

    Mirrors turbine-sdk `auto({ fastSpreadBps, deltaBps?, yoloBps? })`. Anchors:

        windowBps 0     -> yolo_bps                (default -1000)
        windowBps 1000  -> -fast_spread_bps
        windowBps 5000  -> fast_spread_bps - delta_bps
        windowBps 10000 -> fast_spread_bps + delta_bps

    `fast_spread_bps` is required and positive (target "fast"/AMM spread at the
    window end). The curve starts maker-favorable and ramps to a positive
    (pay-to-fill) endpoint so the order reliably settles within its window.

    Defaults: delta_bps = max(1, round(fast_spread_bps * 0.2)); yolo_bps = -1000.
    Guards: yolo_bps < -fast_spread_bps; delta_bps < 2 * fast_spread_bps;
    fast_spread_bps + delta_bps <= MAX_DELTA_BPS.
    """
    if not isinstance(fast_spread_bps, int) or isinstance(fast_spread_bps, bool):
        raise ValueError(f"fast_spread_bps must be an int, got {fast_spread_bps!r}")
    if fast_spread_bps < 1 or fast_spread_bps > MAX_DELTA_BPS:
        raise ValueError(
            f"fast_spread_bps must be in [1, {MAX_DELTA_BPS}], got {fast_spread_bps}"
        )
    if delta_bps is None:
        delta_bps = max(1, round(fast_spread_bps * 0.2))
    if not isinstance(delta_bps, int) or isinstance(delta_bps, bool):
        raise ValueError(f"delta_bps must be an int, got {delta_bps!r}")
    if delta_bps < 1 or delta_bps > MAX_DELTA_BPS:
        raise ValueError(
            f"delta_bps must be in [1, {MAX_DELTA_BPS}], got {delta_bps}"
        )
    _check_delta_in_domain(yolo_bps, "yolo_bps")
    if yolo_bps >= -fast_spread_bps:
        raise ValueError(
            f"auto-spread requires yolo_bps ({yolo_bps}) < -fast_spread_bps ({-fast_spread_bps})"
        )
    if delta_bps >= 2 * fast_spread_bps:
        raise ValueError(
            f"auto-spread requires delta_bps ({delta_bps}) < 2 * fast_spread_bps ({2 * fast_spread_bps})"
        )
    if fast_spread_bps + delta_bps > MAX_DELTA_BPS:
        raise ValueError(
            f"fast_spread_bps + delta_bps = {fast_spread_bps + delta_bps} "
            f"exceeds MAX_DELTA_BPS={MAX_DELTA_BPS}"
        )
    return {
        "startDeltaBps": yolo_bps,
        "endDeltaBps": fast_spread_bps + delta_bps,
        "points": [
            {"windowBps": 1000, "deltaBps": -fast_spread_bps},
            {"windowBps": 5000, "deltaBps": fast_spread_bps - delta_bps},
        ],
    }


def parse_bigint(value: Any) -> int:
    """Turbine serializes bigints as hex-prefixed strings in responses."""
    if isinstance(value, bool):
        raise ValueError(f"bool cannot be bigint: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)  # auto-detect 0x / decimal
    raise ValueError(f"unexpected bigint type: {type(value).__name__}")


def structured_signature_from_bytes(sig: bytes) -> StructuredSignature:
    if len(sig) != 65:
        raise ValueError(f"expected 65-byte signature, got {len(sig)}")
    r = "0x" + sig[0:32].hex()
    s = "0x" + sig[32:64].hex()
    v = sig[64]
    if v in (0, 1):
        v += 27
    if v not in (27, 28):
        raise ValueError(f"unexpected v: {v}")
    return StructuredSignature(r=r, s=s, y_parity=(v == 28), v=hex(v))


def build_siwe_message(
    nonce: str,
    address: str,
    issued_at: Optional[str] = None,
) -> str:
    if issued_at is None:
        issued_at = (
            datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
    return (
        f"{PIN_SIWE_DOMAIN} wants you to sign in with your Ethereum account:\n"
        f"{to_checksum_address(address)}\n"
        f"\n"
        f"Sign in to Turbine with your Ethereum wallet\n"
        f"\n"
        f"URI: {PIN_SIWE_URI}\n"
        f"Version: 1\n"
        f"Chain ID: {CHAIN_ID}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}"
    )


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------


ERC20_ALLOWANCE_ABI = [
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

PERMIT2_ALLOWANCE_ABI = [
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "token", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [
            {"name": "amount", "type": "uint160"},
            {"name": "expiration", "type": "uint48"},
            {"name": "nonce", "type": "uint48"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


class MinimalTurbineClient:
    def __init__(self, account: Account):
        self.account = account
        self.session = requests.Session()
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
        self.settler_address: Optional[str] = None

    # ---------- low-level HTTP

    def _req(self, method: str, path: str, json_body: Any = None) -> Any:
        response = self.session.request(
            method,
            f"{API_URL}/{path}",
            json=json_body,
            timeout=10,
            allow_redirects=False,
        )
        if response.status_code >= 300:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise RuntimeError(
                f"HTTP {response.status_code} on {method} {path}: {body}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    # ---------- /status and /config

    def check_status(self) -> None:
        text = self._req("GET", "status")
        if "Turbine" not in (text or ""):
            raise RuntimeError(f"unexpected /status body: {text!r}")

    def fetch_config_and_pin(self) -> dict:
        cfg = self._req("GET", "config")
        assert isinstance(cfg, dict), f"/config returned non-dict: {cfg!r}"
        mismatches = []
        if cfg["turbineSettlerAddress"].lower() != PIN_SETTLER:
            mismatches.append(("settler", cfg["turbineSettlerAddress"]))
        if cfg["turbineSignerAddress"].lower() != PIN_SIGNER:
            mismatches.append(("signer", cfg["turbineSignerAddress"]))
        if cfg["lpHookAddress"].lower() != PIN_LP_HOOK:
            mismatches.append(("lpHook", cfg["lpHookAddress"]))
        if cfg["lpRouterAddress"].lower() != PIN_LP_ROUTER:
            mismatches.append(("lpRouter", cfg["lpRouterAddress"]))
        if cfg["poolManagerAddress"].lower() != PIN_POOL_MANAGER:
            mismatches.append(("poolManager", cfg["poolManagerAddress"]))
        if cfg["siweDomain"] != PIN_SIWE_DOMAIN:
            mismatches.append(("siweDomain", cfg["siweDomain"]))
        if cfg["siweUri"] != PIN_SIWE_URI:
            mismatches.append(("siweUri", cfg["siweUri"]))
        if mismatches:
            raise RuntimeError(f"config pin mismatch: {mismatches}")
        self.settler_address = cfg["turbineSettlerAddress"]
        return cfg

    # ---------- SIWE auth

    def authenticate(self) -> None:
        nonce = self._req("POST", "nonce")
        assert isinstance(nonce, str), f"/nonce returned non-string: {nonce!r}"
        message = build_siwe_message(nonce=nonce, address=self.account.address)
        signed = self.account.sign_message(encode_defunct(text=message))
        sig = structured_signature_from_bytes(signed.signature)
        self._req("POST", "verify", json_body={
            "message": message,
            "signature": sig.to_siwe_json(),  # SIWE wants yParity as hex string
        })

    def me(self) -> dict:
        return self._req("GET", "me")

    # ---------- Permit2

    def read_erc20_allowance_to_permit2(self, token: str) -> int:
        contract = self.w3.eth.contract(
            address=to_checksum_address(token), abi=ERC20_ALLOWANCE_ABI
        )
        return int(contract.functions.allowance(
            to_checksum_address(self.account.address),
            to_checksum_address(PERMIT2),
        ).call())

    def read_permit2_nonce(self, token: str) -> int:
        contract = self.w3.eth.contract(
            address=to_checksum_address(PERMIT2), abi=PERMIT2_ALLOWANCE_ABI
        )
        _, _, nonce = contract.functions.allowance(
            to_checksum_address(self.account.address),
            to_checksum_address(token),
            to_checksum_address(self.settler_address),
        ).call()
        return int(nonce)

    def sign_permit2(self, token: str, end_time: int) -> tuple[dict, StructuredSignature]:
        nonce = self.read_permit2_nonce(token)
        typed_data = {
            "domain": {
                "name": "Permit2",
                "chainId": CHAIN_ID,
                "verifyingContract": to_checksum_address(PERMIT2),
            },
            "primaryType": "PermitSingle",
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "PermitSingle": [
                    {"name": "details", "type": "PermitDetails"},
                    {"name": "spender", "type": "address"},
                    {"name": "sigDeadline", "type": "uint256"},
                ],
                "PermitDetails": [
                    {"name": "token", "type": "address"},
                    {"name": "amount", "type": "uint160"},
                    {"name": "expiration", "type": "uint48"},
                    {"name": "nonce", "type": "uint48"},
                ],
            },
            "message": {
                "details": {
                    "token": to_checksum_address(token),
                    "amount": MAX_UINT160,
                    "expiration": end_time,
                    "nonce": nonce,
                },
                "spender": to_checksum_address(self.settler_address),
                "sigDeadline": end_time,
            },
        }
        signed = self.account.sign_typed_data(full_message=typed_data)
        sig = structured_signature_from_bytes(signed.signature)
        permit_json = {
            "details": {
                "token": to_checksum_address(token),
                "amount": str(MAX_UINT160),
                "expiration": end_time,
                "nonce": nonce,
            },
            "spender": to_checksum_address(self.settler_address),
            "sigDeadline": str(end_time),
        }
        return permit_json, sig

    # ---------- Orders

    def quote_fee(self, intent: OrderIntent) -> int:
        raw = self._req("POST", "order_fees", json_body=intent.to_json())
        return parse_bigint(raw)

    def add_order(self, intent: OrderIntent) -> str:
        permit_json, sig = self.sign_permit2(intent.sell_token, intent.end_time)
        payload = {
            "order": intent.to_json(),
            "signedPermit": {
                "signature": sig.to_permit2_json(),  # /add_order wants yParity as JSON bool
                "permit": permit_json,
            },
        }
        raw = self._req("POST", "add_order", json_body=payload)
        return raw["orderHash"]

    def cancel_order(self, order_hash: str) -> str:
        raw = self._req("POST", "cancel_order", json_body={"orderHash": order_hash})
        return raw["orderHash"]

    def get_order_states(self, order_hashes: list[str]) -> list[dict]:
        # Returns one entry per requested hash. v0.114 OrderStatus values:
        #   Active | Filled | PendingCancellation | Canceled | Invalid |
        #   Expired | Adding | PartiallyFilled | Unknown
        #   (Adding = order being added, pre-Active.)
        # Execution amounts may appear camelCase (soldAmount / boughtAmount /
        # surplusBoughtAmount) in addition to legacy snake_case — read both
        # tolerantly. The exact per-entry shape is not asserted here; if you
        # observe a different shape, please open a PR against the docs.
        raw = self._req("POST", "order_states", json_body={"orderHashes": order_hashes})
        assert isinstance(raw, list)
        return raw


# ----------------------------------------------------------------------
# Example usage
# ----------------------------------------------------------------------


def main() -> int:
    priv_key = os.environ.get("WALLET_PRIVATE_KEY")
    if not priv_key:
        print("FATAL: set WALLET_PRIVATE_KEY env var", file=sys.stderr)
        return 1

    # Your wallet is derived from WALLET_PRIVATE_KEY; account.address below is
    # the address that owns the orders (e.g. 0x1111...1111 — set your own).
    account = Account.from_key(priv_key)
    client = MinimalTurbineClient(account)

    log.info("wallet: %s", account.address)

    log.info("checking /status ...")
    client.check_status()
    log.info("checking /config with pins ...")
    client.fetch_config_and_pin()

    log.info("authenticating via SIWE ...")
    client.authenticate()
    me = client.me()
    # /api/me returns {"authenticated": bool, "address": "0x..."} where address
    # is your own wallet (e.g. 0x1111111111111111111111111111111111111111).
    log.info("me: %s", me)
    assert me.get("authenticated"), "SIWE auth failed"

    # Build a small sell-side intent (WETH -> USDC).
    # Must be worth >= $30 USD or the server will reject it.
    now = int(time.time())
    intent = OrderIntent(
        owner=account.address,
        sell_token=WETH,
        buy_token=USDC,
        sell_amount=int(0.016 * 10**18),  # ~$35 at $2200/ETH
        min_buy_amount=33 * 10**6,         # 33 USDC floor
        # Flat -10 bps curve (maker better than mid). Swap in spread_auto(...)
        # for a 4-knot auto-spread order that ramps to a pay-to-fill endpoint.
        spread_curve=spread_constant(-10),
        start_time=now,
        end_time=now + 3600,
        salt=random_salt(),
    )

    log.info("quoting fee for the intent ...")
    fee_atomic = client.quote_fee(intent)
    log.info("platform fee: %s buy-token atomic units", fee_atomic)
    # /api/order_fees returns the fee in buy-token atomic units — treat it as
    # the source of truth rather than assuming any fixed bps figure.

    log.info("verifying ERC-20 allowance to Permit2 ...")
    erc_allowance = client.read_erc20_allowance_to_permit2(WETH)
    if erc_allowance == 0:
        log.error(
            "ERC-20 allowance to Permit2 is zero. Send a one-time "
            "WETH.approve(PERMIT2, max) from your wallet first."
        )
        return 1
    log.info("allowance = %s (OK)", erc_allowance)

    log.info("submitting order ...")
    order_hash = client.add_order(intent)
    log.info("order_hash: %s", order_hash)

    log.info("polling once ...")
    states = client.get_order_states([order_hash])
    log.info("states: %s", json.dumps(states, indent=2))

    log.info("cancelling so we do not leave it hanging ...")
    client.cancel_order(order_hash)
    log.info("cancelled")

    return 0


if __name__ == "__main__":
    sys.exit(main())
