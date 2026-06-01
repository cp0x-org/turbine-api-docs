# Permit2 and order signing

Turbine uses [Uniswap Permit2](https://github.com/Uniswap/permit2)
for token transfer authorization. You approve Permit2 once at the
ERC-20 level; after that, every Turbine order carries an off-chain
signed `PermitSingle` message telling Permit2 that the Turbine
Settler is authorized to transfer a specific amount within a specific
deadline.

**The `OrderIntent` is not EIP-712 signed.** The only typed-data
signature in the Turbine flow is the Permit2 `PermitSingle`.

The `PermitSingle` **spender** is the Turbine Settler. Read its
address from `GET /api/config` (`turbineSettlerAddress`) rather than
hardcoding it — it changes on redeploy. As of API `v0.114.1` it is
`0xbb3e81c0563dc61719696475f5c7b5e011a73f8a`.

## One-time setup: ERC-20 approval

Before you can trade a token through Turbine, you need a standard
ERC-20 allowance from your wallet to the Permit2 contract:

```solidity
IERC20(token).approve(PERMIT2, type(uint256).max)
```

Permit2 contract: `0x000000000022D473030F116dDEE9F6B43aC78BA3`.

Do this once per token, through Rabby / MetaMask / whatever you use.
Max-uint256 is standard. You can revoke any time by calling
`approve(PERMIT2, 0)`.

### Verifying the approval

```python
from web3 import Web3
w3 = Web3(Web3.HTTPProvider("https://rpc.mevblocker.io"))
weth = w3.eth.contract(
    address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    abi=[{
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }],
)
allowance = weth.functions.allowance(
    YOUR_WALLET,
    "0x000000000022D473030F116dDEE9F6B43aC78BA3",
).call()
assert allowance > 0
```

This is the allowance your client should check at startup and in
periodic reconciliation. **Do not** read Permit2's own
`allowance(owner, token, spender)` as a proxy for approval — that
view returns `(0, 0, 0)` until the first signed Permit2 message is
consumed, which is misleading as a "not approved" signal.

## Per-order: sign `PermitSingle`

For every `OrderIntent`, before POSTing to `/api/add_order`:

1. Read the current Permit2 nonce for
   `(owner, sellToken, Turbine Settler)`.
2. Build a `PermitSingle` struct with that nonce, the sell token, the
   amount, and an expiration.
3. EIP-712 sign the `PermitSingle` using
   `eth_signTypedData_v4`.
4. Convert the 65-byte signature into
   `{r, s, yParity, v}` format **with `yParity` as a boolean**.
5. Include it inside the order payload alongside the permit struct.

### Step 1: read the Permit2 nonce

```python
permit2 = w3.eth.contract(
    address="0x000000000022D473030F116dDEE9F6B43aC78BA3",
    abi=[{
        "inputs": [
            {"name": "owner",  "type": "address"},
            {"name": "token",  "type": "address"},
            {"name": "spender","type": "address"},
        ],
        "name": "allowance",
        "outputs": [
            {"name": "amount",     "type": "uint160"},
            {"name": "expiration", "type": "uint48"},
            {"name": "nonce",      "type": "uint48"},
        ],
        "stateMutability": "view",
        "type": "function",
    }],
)
_, _, nonce = permit2.functions.allowance(
    wallet_address,
    sell_token,
    settler_address,   # 0xbb3e81c0563dc61719696475f5c7b5e011a73f8a
).call()
```

The nonce starts at 0 and increments each time a signed permit is
consumed on-chain.

### Step 2: build and sign the typed data

```python
from eth_account import Account

MAX_UINT160 = (1 << 160) - 1

typed_data = {
    "domain": {
        "name": "Permit2",
        "chainId": 1,
        "verifyingContract": "0x000000000022D473030F116dDEE9F6B43aC78BA3",
    },
    "primaryType": "PermitSingle",
    "types": {
        "EIP712Domain": [
            {"name": "name",              "type": "string"},
            {"name": "chainId",           "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "PermitSingle": [
            {"name": "details",     "type": "PermitDetails"},
            {"name": "spender",     "type": "address"},
            {"name": "sigDeadline", "type": "uint256"},
        ],
        "PermitDetails": [
            {"name": "token",      "type": "address"},
            {"name": "amount",     "type": "uint160"},
            {"name": "expiration", "type": "uint48"},
            {"name": "nonce",      "type": "uint48"},
        ],
    },
    "message": {
        "details": {
            "token":      sell_token,       # checksummed
            "amount":     MAX_UINT160,      # or a tighter per-order cap
            "expiration": order_end_time,   # Unix seconds
            "nonce":      nonce,            # from step 1
        },
        "spender":     settler_address,
        "sigDeadline": order_end_time,
    },
}

signed = account.sign_typed_data(full_message=typed_data)
sig_bytes = signed.signature  # 65 bytes
```

### Step 3: convert the signature to structured form

Turbine's backend expects a structured signature object, not the raw
65-byte blob:

```python
r = "0x" + sig_bytes[0:32].hex()
s = "0x" + sig_bytes[32:64].hex()
v = sig_bytes[64]
if v in (0, 1):          # normalize EIP-2098
    v += 27
assert v in (27, 28)

structured = {
    "r":       r,
    "s":       s,
    "yParity": (v == 28),      # JSON boolean! (see wire-format.md)
    "v":       hex(v),          # "0x1b" or "0x1c"
}
```

**Critical:** `yParity` on the `/add_order` endpoint is a JSON
**boolean**. Sending `"0x0"` / `"0x1"` fails with HTTP 422. The
`/api/verify` endpoint wants the opposite convention (string). See
[wire-format.md](wire-format.md).

### Step 4: attach to the order payload

```python
add_order_body = {
    "order":         intent.to_json(),
    "signedPermit": {
        "signature": structured,
        "permit": {
            "details": {
                "token":      sell_token,
                "amount":     str(MAX_UINT160),
                "expiration": order_end_time,
                "nonce":      nonce,
            },
            "spender":     settler_address,
            "sigDeadline": str(order_end_time),
        },
    },
}
```

POST this to `/api/add_order`. If everything lines up, the response
is `{"orderHash": "0x..."}` and you can poll that hash.

## Nonce semantics

Permit2's `allowance(owner, token, spender)` returns
`(amount, expiration, nonce)`. The nonce **only increments when the
Settler actually consumes a signed permit** (i.e. when a fill
settles on-chain). Resting orders do not advance the nonce. For a
passive market maker this means:

* Sign every order with the *current* nonce.
* If an order never fills (auto-expires), its signed permit is simply
  never used — the nonce stays where it was.
* If multiple orders are signed with the same nonce and one fills,
  the others become invalid — but that scenario is unusual for a
  single-order-per-slot strategy.

The official SDK does not enforce nonce uniqueness across concurrent
orders; it reads the current Permit2 nonce fresh for every sign call.

## Signing the same permit amount for many orders

Permit2 allows the `amount` field in `PermitSingle` to be reused up
to the full approved allowance. Using `amount = maxUint160` (the
"infinite" convention) and a short `sigDeadline` per order is the
common pattern. Per-order caps also work if you want finer control.

## Emergency revoke

If anything goes wrong — you want to kill a running bot, you suspect
a compromised settler address, you are leaving the platform — revoke
Permit2's ERC-20 allowance:

```solidity
IERC20(token).approve(PERMIT2, 0)
```

That invalidates all future `transferFrom` calls through Permit2
regardless of any signed-but-unsettled `PermitSingle` messages. Do
this per token. Cost is ~1 gas-unit approval per token.
