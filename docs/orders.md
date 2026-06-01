# Orders — `OrderIntent` fields and semantics

Turbine orders are **signed off-chain intents**, not individual
transactions. You construct an `OrderIntent`, sign a Permit2 message
authorising Turbine's Settler to move the `sellToken`, and POST the
two together to `/api/add_order`. The intent itself is **not**
EIP-712 signed — only the Permit2 permit is.

## Field reference

```jsonc
{
  "owner":          "0x1111111111111111111111111111111111111111", // <your wallet address>, must match SIWE-authed wallet
  "sellToken":      "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", // EIP-55 checksummed address
  "buyToken":       "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", // EIP-55 checksummed, != sellToken
  "sellAmount":     "16000000000000000",  // string, atomic units, > 0
  "minBuyAmount":   "33000000",           // string, atomic units, > 0 — hard floor on execution
  "spreadCurve":    { "startDeltaBps": -10, "endDeltaBps": -10, "points": [] }, // delta curve over the order window (replaces midPriceDelta)
  "startTime":      "1776048000",         // string, unix seconds
  "endTime":        "1776051600",         // string, unix seconds, > startTime, in the future
  "partialFill":    true,                 // bool, frontend always sets true
  "callData":       "0x",                 // hex string, 0x for non-smart orders
  "callDataTarget": "0x0000000000000000000000000000000000000000", // address, 0x0 for non-smart
  "salt":           "0x1111111111111111111111111111111111111111111111111111111111111111"  // 32 random bytes as hex
}
```

### `owner`

Must match the wallet that authenticated via SIWE. The server
verifies this; the SDK also checks it client-side before signing and
raises `UNAUTHORIZED` locally.

### `sellToken` / `buyToken`

Must be different. Use EIP-55 checksummed addresses
(`Web3.to_checksum_address` in Python). The full set of supported tokens
is returned by `GET /api/config` (each entry carries CEX oracle mappings).
Do not hardcode the list — read it from `/api/config`. As of v0.114 it is
much larger than earlier deployments. Two common examples are
WETH (`0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2`) and
USDC (`0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`).

### `sellAmount`

Atomic units (not decimal tokens) as a **string**. Must be > 0. This
is how much of `sellToken` the solver is authorised to consume. Partial
fills draw down from this pool up to `sellAmount`.

### `minBuyAmount`

Atomic units of the `buyToken` as a **string**. Must be > 0. This is
a **hard floor** — the solver will refuse to fill the order at any
price that would yield less than `minBuyAmount` total.

For a passive MM order, set this close to the mid-price-equivalent
amount minus a small safety buffer. Example: selling 0.016 WETH at a
mid of $2192.63 expects ~35.08 USDC. Setting `minBuyAmount = 33
USDC` gives a ~5.9% safety floor below mid and ensures you do not
get picked off during a price crash between build and settlement.

### `spreadCurve`

**Replaces the old scalar `midPriceDelta` field (removed in v0.114).**
Instead of one fixed delta, an order now carries a **delta curve over
its own time window** — the price offset the solver applies can change
between `startTime` and `endTime`.

```jsonc
"spreadCurve": {
  "startDeltaBps": -10,   // int, delta at windowBps = 0 (= startTime)
  "endDeltaBps":   -10,   // int, delta at windowBps = 10000 (= endTime)
  "points": [             // interior knots, may be empty
    { "windowBps": 5000, "deltaBps": -5 }
  ]
}
```

* **`windowBps`** = normalized order-window *time*, not price. `0` is
  `startTime`, `10000` is `endTime`; interior knots live in `[1, 9999]`.
* **`deltaBps`** = signed delta, **1 unit = 1 basis point (0.01%)**.
  Domain is `[MIN_DELTA_BPS = -10000, MAX_DELTA_BPS = 9999]`.
  * **Negative = "I demand better than mid price"** — market maker /
    earn side. Example `-15` means "fill me at no worse than 15 bps
    better than the current mid". This is where your spread lives.
  * **Positive = "I tolerate worse than mid price"** — taker / fast
    side. Example `+50` means "fill me even if the price is 50 bps
    worse than mid". This is where urgency buys execution certainty.
  * **Zero** = fill at exactly the mid price.
* The **effective delta at time `now`** is the piecewise-linear
  interpolation between knots (`startDeltaBps`, the `points`, and
  `endDeltaBps`).
* `points.length` is capped at `MAX_SPREAD_CURVE_POINTS = 1024` (an SDK
  DoS guard; the backend enforces a tighter bound based on order
  duration and block interval).

`deltaBps`/`startDeltaBps`/`endDeltaBps` are sent as **JSON integers**,
not strings.

#### Building a curve

The SDK ships two helpers (`turbine-sdk/src/spreads.ts`) that cover the
common cases.

**`constant(deltaBps)`** — a flat curve that returns the same delta for
the whole window. This is exactly the old fixed-`midPriceDelta`
behavior:

```jsonc
// constant(-8)
"spreadCurve": { "startDeltaBps": -8, "endDeltaBps": -8, "points": [] }
```

**`auto({ fastSpreadBps, deltaBps?, yoloBps? })`** — a 4-knot ramp, the
new **auto-spread** order type. It starts maker-favorable and ramps to a
positive (pay-to-fill) delta at the end of the window, so the order
reliably settles within its window instead of going stale:

| `windowBps` | `deltaBps`               |
|-------------|--------------------------|
| 0 (start)   | `yoloBps` (default −1000) |
| 1000        | `−fastSpreadBps`         |
| 5000        | `fastSpreadBps − deltaBps`|
| 10000 (end) | `fastSpreadBps + deltaBps`|

* `fastSpreadBps` is **required and positive** (≥ 1) — the target
  "fast"/AMM spread reached at the end of the window.
* `deltaBps` (the auto parameter) defaults to
  `max(1, round(fastSpreadBps * 0.2))`.
* `yoloBps` defaults to `−1000`.
* Validation guards (raise on violation):
  `yoloBps < −fastSpreadBps`; `deltaBps < 2 * fastSpreadBps`;
  `fastSpreadBps + deltaBps ≤ 9999`.

```jsonc
// auto({ fastSpreadBps: 10 }) -> deltaBps defaults to 2, yoloBps to -1000
"spreadCurve": {
  "startDeltaBps": -1000,
  "endDeltaBps":   12,
  "points": [
    { "windowBps": 1000, "deltaBps": -10 },
    { "windowBps": 5000, "deltaBps":  8 }
  ]
}
```

Unlike a flat `constant(...)` curve, an `auto` order is designed to
execute (it ends at a positive, pay-to-fill delta), so it is not a pure
passive maker order.

### `startTime` / `endTime`

Unix seconds as **strings**. `startTime` may be the current time or
in the past; `endTime` must be in the future at submit time and
strictly greater than `startTime`. The typical value for `endTime` is
`now + 3600` (1 hour). Orders past `endTime` auto-expire.

### `partialFill`

Bool. The frontend always sets `true`. Setting `false` would mean
"all-or-nothing fill" — unclear whether the solver supports this path
in production, but the field exists in the SDK typing.

### `callData` / `callDataTarget`

For **smart orders** (intents with a post-execution contract
callback), `callData` is non-empty hex and `callDataTarget` is the
address to call back. For normal trades, both are zeroed:
`callData = "0x"`, `callDataTarget = 0x0`.

### `salt`

32 random bytes as a `0x`-prefixed hex string. The SDK generates this
with `crypto.getRandomValues(new Uint8Array(32))`. Salt ensures
uniqueness of the intent hash across identical-parameters orders.

## Server-side constraints

Verified on mainnet via production probes on 2026-04-13.

* **Minimum order value: $30 USD**. Both sides. Smaller orders return
  HTTP 400 with `INPUT_VALIDATION_ERROR` and message
  `"Sell amount X is worth ~$Y which is less than $30"`.
* **Max pending orders: 5** (front-end hard-coded; server enforcement
  not confirmed). If you run a bot, respect the 5 cap.
* **`endTime` must be strictly in the future at submit.** A past
  `endTime` is a local SDK validation failure, not a server one.

## `add_order` wire payload

`POST /api/add_order` takes the `OrderIntent` plus a signed Permit2
permit. For a standard (non-smart) order the body is:

```jsonc
{
  "order": {
    "owner", "sellToken", "buyToken", "sellAmount", "minBuyAmount",
    "spreadCurve": { "startDeltaBps": ..., "endDeltaBps": ..., "points": [ ... ] },
    "startTime", "endTime", "partialFill", "callData", "callDataTarget", "salt"
  },
  "signedPermit": {
    "signature": { "r": "0x...", "s": "0x...", "yParity": true, "v": "0x1c" },
    "permit": {
      "details": { "token": "0x...", "amount": "0x...", "expiration": ..., "nonce": ... },
      "spender": "0xbb3e81c0563dc61719696475f5c7b5e011a73f8a", // turbineSettlerAddress
      "sigDeadline": "0x..."
    }
  }
}
```

* The permit **`spender` is the `turbineSettlerAddress`** from
  `/api/config` (`0xbb3e81c0563dc61719696475f5c7b5e011a73f8a` on
  v0.114.1). Read it from config rather than hardcoding it.
* `BigInt` values serialize as `0x`-prefixed hex strings.
* **Smart orders** (`callData != "0x"` and
  `callDataTarget != 0x0`) omit `signedPermit` entirely — the body is
  just `{ "order": { ... } }` — because they handle their own token
  transfers.
* The `order_fees` request body is the raw `OrderIntent` on its own (no
  permit wrapper); see [fees.md](./fees.md).

## Lifecycle and state tracking

A successful `/api/add_order` returns:

```json
{ "orderHash": "0x1111111111111111111111111111111111111111111111111111111111111111" }
```

That 32-byte hash is the authoritative ID. Track it through
`/api/order_states` (POST `{ "orderHashes": ["0x..."] }`, returns an
array of order states):

```jsonc
{
  "hash": "0x1111...",
  "status": "Active",
  "execution": [
    {
      "txHash":            "0x...",
      "blockNumber":       18123456,
      "soldAmount":        "0x0",
      "boughtAmount":      "0x0",
      "surplusBuyAmount":  "0x0"
    }
  ]
}
```

Notes:

* `status` is one of the **`OrderStatus`** values (TitleCase) listed
  below. `"Active"` is the resting state.
* In v0.114 the SDK parses execution amounts from **camelCase** fields
  (`soldAmount`, `boughtAmount`, `surplusBuyAmount`, `txHash`,
  `blockNumber`). Older deployments returned snake_case
  (`sold_amount`, `bought_amount`, `surplus_buy_amount`, `tx_hash`,
  `block_number`); parse tolerantly and accept either. The exact
  on-the-wire casing for this deployment is not directly verified here —
  open a PR if you observe it.
* Execution bigint fields are hex-prefixed. Parse with base-0 int.
* `surplusBuyAmount` captures positive slippage vs. `minBuyAmount`
  (solver found a better price). For realized-yield analysis, add
  this to `boughtAmount`.
* The front-end polls `/api/order_states` on a short interval with a
  client-side mutex that prevents overlapping polls. Mimic that
  pattern.

### `OrderStatus` values (v0.114)

From `types/orders.ts`:

| Status                | Meaning                                            |
|-----------------------|----------------------------------------------------|
| `Adding`              | Order is being added (pre-`Active`)                |
| `Active`              | Resting, open for fills                            |
| `PartiallyFilled`     | Some of `sellAmount` filled, order still live      |
| `Filled`              | Fully filled                                       |
| `PendingCancellation` | Cancellation requested, not yet finalized          |
| `Canceled`            | Cancelled (note the single-`l` spelling)           |
| `Expired`             | Past `endTime` without (full) fill                 |
| `Invalid`             | Order rejected/invalid                             |
| `Unknown`             | Fallback / unrecognized                            |

`Adding`, `Active`, and `PendingCancellation` are the "pending" states
in the SDK.

## Cancellation

```
POST /api/cancel_order
{ "orderHash": "0x..." }
```

Succeeds with the same hash echoed back. Cancellation has been observed
to take effect immediately server-side. What happens if the solver is
already mid-fill is not directly verified — open an issue or PR if you
observe it.
