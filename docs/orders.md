# Orders — `OrderIntent` fields and semantics

Turbine orders are **signed off-chain intents**, not individual
transactions. You construct an `OrderIntent`, sign a Permit2 message
authorising Turbine's Settler to move the `sellToken`, and POST the
two together to `/api/add_order`. The intent itself is **not**
EIP-712 signed — only the Permit2 permit is.

## Field reference

```jsonc
{
  "owner":          "0x1111111111111111111111111111111111111111", // must match SIWE-authed wallet
  "sellToken":      "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", // EIP-55 checksummed address
  "buyToken":       "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", // EIP-55 checksummed, != sellToken
  "sellAmount":     "16000000000000000",  // string, atomic units, > 0
  "minBuyAmount":   "33000000",           // string, atomic units, > 0 — hard floor on execution
  "midPriceDelta":  -10,                  // int16 bps, -10000..+10000
  "startTime":      "1776048000",         // string, unix seconds
  "endTime":        "1776051600",         // string, unix seconds, > startTime, in the future
  "partialFill":    true,                 // bool, frontend always sets true
  "callData":       "0x",                 // hex string, 0x for non-smart orders
  "callDataTarget": "0x0000000000000000000000000000000000000000", // address, 0x0 for non-smart
  "salt":           "0x1111...1111"  // 32 random bytes as hex
}
```

### `owner`

Must match the wallet that authenticated via SIWE. The server
verifies this; the SDK also checks it client-side before signing and
raises `UNAUTHORIZED` locally.

### `sellToken` / `buyToken`

Must be different. Use EIP-55 checksummed addresses
(`Web3.to_checksum_address` in Python). The SDK constants ship with
USDC, USDT, DAI, UNI, WETH, WEETH, PEPE, WBTC on mainnet; the frontend
currently exposes only USDC/WETH.

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

### `midPriceDelta`

Signed integer in **basis points**. Range `-10000..+10000` (±100%).

* **Negative = "I demand better than mid price"** — market maker /
  earn side. Example `-15` means "fill me at no worse than 15 bps
  better than the current mid". This is where your spread lives.
* **Positive = "I tolerate worse than mid price"** — taker / fast
  side. Example `+50` means "fill me even if the price is 50 bps
  worse than mid". This is where urgency buys execution certainty.
* **Zero** = fill at exactly the mid price.

This value is sent as a **JSON integer**, not a string. The SDK uses
`Decimal` percentages in its frontend abstraction (e.g. user enters
`0.0028` for 28 bps and the SDK multiplies by 10,000 and rounds).

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

## Lifecycle and state tracking

A successful `/api/add_order` returns:

```json
{ "orderHash": "0x1111111111111111111111111111111111111111111111111111111111111111" }
```

That 32-byte hash is the authoritative ID. Track it through
`/api/order_states`:

```json
{
  "hash": "0xe5e7...",
  "status": "Active",
  "execution": [
    {
      "tx_hash":           "0x...",
      "block_number":      18123456,
      "sold_amount":       "0x0",
      "bought_amount":     "0x0",
      "surplus_buy_amount":"0x0"
    }
  ]
}
```

Notes:

* `status` is **TitleCase**. `"Active"` is the resting state we've
  observed. Other plausible values (`Filled`, `PartiallyFilled`,
  `Cancelled`, `Expired`) are inferred from the SDK's TypeScript
  typing but not yet observed directly.
* Execution bigint fields are hex-prefixed. Parse with base-0 int.
* `surplus_buy_amount` captures positive slippage vs. `minBuyAmount`
  (solver found a better price). For realized-yield analysis, add
  this to `bought_amount`.
* The front-end polls `/api/order_states` every 6 seconds with a
  client-side mutex that prevents overlapping polls. Mimic that
  pattern.

## Cancellation

```
POST /api/cancel_order
{ "orderHash": "0x..." }
```

Succeeds with the same hash echoed back. In our testing, cancellation
took effect immediately server-side. If the solver is already in the
middle of a fill, we have not observed what happens — open an issue
if you do.
