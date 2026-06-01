# API reference

Base URL: `https://api.turbine.exchange/api`

All authenticated endpoints (everything except `/status` and `/config`)
require a valid SIWE session cookie — see
[authentication.md](authentication.md).

All request and response bodies are JSON unless stated otherwise.

Bigint fields in responses are serialized as `0x`-prefixed hex strings
(the official JS SDK's `bigIntReplacer`). Input accepts both decimal
and hex strings; strictly hex for inputs is safer because the SDK
uses it. See [wire-format.md](wire-format.md).

## Unauthenticated

### `GET /api/status`

Health check. Returns text `"OK from Turbine"` with HTTP 200.

### `GET /api/config`

Returns the runtime contract addresses and SIWE fields. Call this on
every startup, verify the returned values against hard-coded pins,
and halt if they do not match.

```json
{
  "version": "0.114.1",
  "turbineSettlerAddress": "0xbb3e81c0563dc61719696475f5c7b5e011a73f8a",
  "turbineSignerAddress": "0x89c740fea6bd1df86d0f8dff3f4c4c23cb598890",
  "lpHookAddress": "0xa44ff524f78858e015fcca322cb7d16aeb89a088",
  "lpRouterAddress": "0x8e7cc22eda4e2d3a8275fd88cf061681b42ce3d1",
  "poolManagerAddress": "0x000000000004444c5dc75cb358380d2e3de08a90",
  "submitSettlements": true,
  "siweDomain": "app.turbine.exchange",
  "siweUri": "https://api.turbine.exchange/api",
  "tokens": [ /* ... */ ]
}
```

`version` reports the deployment (`0.114.1` at the time of writing).
`config.tokens` is the supported-token list returned by the server
(~401 entries, each with CEX oracle mappings such as
`binance`/`bingx`/`bitget`/`coinbase`/`kraken`/`kucoin`/`okx`). Do not
hard-code it — read it from `/api/config`. The pair used throughout
these examples is WETH
(`0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2`) / USDC
(`0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`).

## Authentication

### `POST /api/nonce`

No body. Returns a JSON string:

```json
"b3a9f0e1c8d7a6b5..."
```

Consume it in the next `/verify` call — nonces are single-use.

### `POST /api/verify`

Body:

```json
{
  "message": "<SIWE message text>",
  "signature": {
    "r": "0x...",
    "s": "0x...",
    "yParity": "0x0",
    "v": "0x1b"
  }
}
```

Empty-body 200 on success, sets cookie. See
[authentication.md](authentication.md) for full details.

### `GET /api/me`

```json
{
  "authenticated": true,
  "address": "0x1111111111111111111111111111111111111111"
}
```

`address` is your authenticated wallet (the placeholder above stands
in for `<your wallet address>`).

### `POST /api/logout`

No body. Clears the server-side session.

## Orders (authenticated)

### `POST /api/add_order`

Submit a single order. Body for standard (non-smart-callback) orders:

```json
{
  "order": {
    "owner": "0x1111111111111111111111111111111111111111",
    "sellToken": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "buyToken":  "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "sellAmount":    "16000000000000000",
    "minBuyAmount":  "33000000",
    "spreadCurve": {
      "startDeltaBps": -10,
      "endDeltaBps":   -10,
      "points":        []
    },
    "startTime":     "1776048000",
    "endTime":       "1776051600",
    "partialFill":   true,
    "callData":      "0x",
    "callDataTarget":"0x0000000000000000000000000000000000000000",
    "salt":          "0x1111111111111111111111111111111111111111111111111111111111111111"
  },
  "signedPermit": {
    "signature": {
      "r":       "0x1111111111111111111111111111111111111111111111111111111111111111",
      "s":       "0x2222222222222222222222222222222222222222222222222222222222222222",
      "yParity": false,
      "v":       "0x1b"
    },
    "permit": {
      "details": {
        "token":      "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "amount":     "1461501637330902918203684832716283019655932542975",
        "expiration": 1776051600,
        "nonce":      0
      },
      "spender":     "0xbb3e81c0563dc61719696475f5c7b5e011a73f8a",
      "sigDeadline": "1776051600"
    }
  }
}
```

The `spreadCurve` above is a flat curve (`startDeltaBps == endDeltaBps`,
no interior `points`), equivalent to the old fixed `midPriceDelta` of
`-10` bps. `spender` equals the `turbineSettlerAddress` returned by
`/api/config`. See the **Spread curves** section below for the full
shape.

Response:

```json
{ "orderHash": "0x1111111111111111111111111111111111111111111111111111111111111111" }
```

Important details:

* **`signedPermit.signature.yParity` is a JSON boolean** (`true` or
  `false`). Sending a string `"0x0"`/`"0x1"` fails with HTTP 422.
  This is different from `/verify` — see
  [wire-format.md](wire-format.md).
* **Minimum order size is enforced at $30 USD**. Smaller values fail
  with HTTP 400 `INPUT_VALIDATION_ERROR` and a message like
  `"Sell amount X is worth ~$19.97 which is less than $30"`.
* **`spreadCurve` replaces the old scalar `midPriceDelta`** (which is
  gone in v0.114). It is a delta curve over the order's time window —
  see **Spread curves** below. Each `deltaBps` is a signed int in
  `[-10000, 9999]` (1 unit = 1 bp = 0.01%); negative is maker-favorable
  ("earn"), positive is pay-to-fill ("fast").
* **Permit2 `amount`** in the example is `maxUint160 = 2^160 - 1`
  (unlimited approval); you can specify a tighter amount if you want
  per-order cap.
* **`callData` / `callDataTarget`** non-empty makes this a "smart
  order"; then `signedPermit` is omitted — see the smart-order section
  below.

#### Spread curves

A `spreadCurve` carries the order's price delta as a piecewise-linear
curve over the order's *normalized* time window:

```json
{
  "startDeltaBps": -10,
  "endDeltaBps":   -10,
  "points": [ { "windowBps": 5000, "deltaBps": -5 } ]
}
```

* `windowBps` is normalized time from `0` (= `startTime`) to `10000`
  (= `endTime`); interior knots are in `[1, 9999]`.
* `deltaBps` is signed, `[-10000, 9999]` (1 unit = 1 bp = 0.01%).
* `startDeltaBps` is the delta at `windowBps = 0`, `endDeltaBps` at
  `windowBps = 10000`. Interior knots go in `points`.
* The effective delta at time `now` is linear interpolation between
  the surrounding knots.
* `points` may hold up to `MAX_SPREAD_CURVE_POINTS = 1024` knots (an
  SDK guard; the backend may be stricter by order duration / block
  interval).

The official JS SDK (`turbine-sdk/src/spreads.ts`) ships two builders:

* **`constant(deltaBps)`** → `{ startDeltaBps: d, endDeltaBps: d,
  points: [] }`. A flat curve — this reproduces the old fixed
  `midPriceDelta` behavior.
* **`auto({ fastSpreadBps, deltaBps?, yoloBps? })`** → a 4-knot ramp
  ("auto-spread", a new order shape). `fastSpreadBps` is **required and
  positive** (the target "fast"/AMM spread reached by the end of the
  window). The anchors are:

  | `windowBps` | `deltaBps`                |
  |-------------|---------------------------|
  | `0`         | `yoloBps`                 |
  | `1000`      | `-fastSpreadBps`          |
  | `5000`      | `fastSpreadBps - deltaBps`|
  | `10000`     | `fastSpreadBps + deltaBps`|

  Defaults: `deltaBps = max(1, round(fastSpreadBps * 0.2))`,
  `yoloBps = -1000`. The builder rejects parameters that break
  monotonicity or exceed the domain: it requires
  `yoloBps < -fastSpreadBps`, `deltaBps < 2 * fastSpreadBps`, and
  `fastSpreadBps + deltaBps <= 9999`.

  An auto curve starts maker-favorable and ramps to a positive
  (pay-to-fill) end, so the order is designed to settle within its
  window rather than rest as a stale limit order.

### `POST /api/add_orders`

Same payload wrapped in an array. Response is an array of
`{ orderHash }`. Each order is independently signed with its own
Permit2 permit.

### `POST /api/cancel_order`

```json
{ "orderHash": "0x1111111111111111111111111111111111111111111111111111111111111111" }
```

Response:

```json
{ "orderHash": "0x1111111111111111111111111111111111111111111111111111111111111111" }
```

### `POST /api/order_states`

```json
{ "orderHashes": [ "0x...", "0x..." ] }
```

Response is an array:

```json
[
  {
    "hash": "0x...",
    "status": "Active",
    "execution": [
      {
        "tx_hash":           "0x...",
        "block_number":      18123456,
        "sold_amount":       "0x1a",
        "bought_amount":     "0x2c",
        "surplus_buy_amount":"0x0"
      }
    ]
  }
]
```

`status` is one of the v0.114 `OrderStatus` values (SDK
`types/orders.ts`):

* `Active` — resting in the book, no fills yet.
* `Adding` — order is being added, pre-`Active`.
* `Filled`
* `PartiallyFilled`
* `PendingCancellation`
* `Canceled`
* `Expired`
* `Invalid`
* `Unknown`

The SDK groups `Adding`, `Active`, and `PendingCancellation` as
"pending". Only `Active` is **directly verified** here; the other
strings are taken from the SDK enum but **not directly verified** at
the time of writing. Please PR if you observe them.

`execution[]` amounts are hex-prefixed bigint strings — parse with a
base-0 int parser. Be tolerant of casing: a legacy snake_case form
(`tx_hash`, `block_number`, `sold_amount`, `bought_amount`,
`surplus_buy_amount`) and a camelCase form (`txHash`, `blockNumber`,
`soldAmount`, `boughtAmount`, and a surplus field — the SDK reads it as
`surplusBuyAmount`) have both been referenced. The official SDK parses
the camelCase shape. The exact on-the-wire casing is **not fully
verified** — read whichever key is present rather than relying on one
form.

### `POST /api/order_fees`

Quote the platform fee for a prospective order **without** submitting
it. Body is the raw `OrderIntent` (no `signedPermit`):

```json
{
  "owner": "0x...",
  "sellToken": "0x...",
  ...
}
```

Response is a hex-prefixed bigint string denominated in the **buy
token's atomic units**:

```json
"0xd8d"
```

Value is `0xd8d = 3469` atomic units in the example. This endpoint is
the source of truth for the fee — quote it per order rather than
assuming a fixed rate. See [fees.md](fees.md) for how to interpret the
returned amount.

## Liquidity endpoints (LP side)

### `POST /api/add_liquidity`

```json
{
  "addLiquidity": { /* AddLiquidityIntent */ },
  "permitTokens": {
    "signature": { "r": "0x...", "s": "0x...", "yParity": false, "v": "0x1b" },
    "permit":    { /* PermitBatch */ }
  }
}
```

### `POST /api/remove_liquidity`

```json
{
  "removeLiquidity": { /* RemoveLiquidityIntent */ },
  "permitLpToken": {
    "signature": { "r": "0x...", "s": "0x...", "yParity": false, "v": "0x1b" },
    "permit":    { /* PermitSingle */ }
  }
}
```

### `POST /api/liquidity_intent_states`

```json
{ "intentHashes": ["0x...", "0x..."] }
```

Response:

```json
[
  { "hash": "0x...", "status": "Pending" }
]
```

Known statuses per the SDK's `LiquidityIntentStatus` enum:
`Pending`, `Invalid`, `Expired`, `Executed`, `PendingCancellation`,
`Canceled`.

LP details (reserve reads, pool registration, permit batch signing)
happen on-chain via the `lpHookAddress` and `lpRouterAddress`
contracts — not covered here. See the SDK source or open an issue if
you need specifics.

## Smart orders

If your `OrderIntent` has `callData != "0x"` **and** `callDataTarget !=
0x0`, the server treats it as a "smart order" (intent with a
post-execution callback). Payload then omits the permit:

```json
{ "order": { /* intent with callData, callDataTarget */ } }
```

No `signedPermit`. The SDK's `TurbineClient.createAddOrderData` makes
this distinction automatically. We have not exercised this path.

## HTTP error shapes

Errors come in two observed forms.

**HTTP 4xx with JSON body:**

```json
{
  "code": "INPUT_VALIDATION_ERROR",
  "message": "Sell amount 9100000000000000 is worth ~$19.971538 which is less than $30"
}
```

**HTTP 4xx with plain-text body (serde deserialization errors):**

```
Failed to deserialize the JSON body into the target type:
signedPermit.signature.yParity: invalid type: string "0x0",
expected a boolean at line 1 column 698
```

Your client should surface both forms. The official SDK wraps them in
`TurbineError` objects.

## Rate limits

Not observed in our testing. If you hit one, please open an issue with
the response headers so we can document it here.
