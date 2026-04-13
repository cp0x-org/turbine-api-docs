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
  "turbineSettlerAddress": "0x49e9a8ea9b6c05d5b2307538d159350a5aea73ac",
  "turbineSignerAddress": "0x89c740fea6bd1df86d0f8dff3f4c4c23cb598890",
  "lpHookAddress": "0x40bd6d8c59d43f6c345d79b17234d9b0e781a088",
  "lpRouterAddress": "0x4bd3f2ffc321f3ba4c3b31708212b76922f805a2",
  "poolManagerAddress": "0x000000000004444c5dc75cb358380d2e3de08a90",
  "submitSettlements": true,
  "siweDomain": "app.turbine.exchange",
  "siweUri": "https://api.turbine.exchange/api"
}
```

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
    "midPriceDelta": -10,
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
      "spender":     "0x49E9A8EA9B6C05d5B2307538D159350A5aEa73aC",
      "sigDeadline": "1776051600"
    }
  }
}
```

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
* **`midPriceDelta` is a signed int**, not a string. Negative values
  (MM "earn" side) and positive values (taker "fast" side) are both
  valid. Range `-10000..+10000` = ±100%.
* **Permit2 `amount`** in the example is `maxUint160 = 2^160 - 1`
  (unlimited approval); you can specify a tighter amount if you want
  per-order cap.
* **`callData` / `callDataTarget`** non-empty makes this a "smart
  order"; then `signedPermit` is omitted — see the smart-order section
  below.

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

`status` strings seen in the wild:

* **`"Active"`** — resting in the book, no fills yet.

Other enum values (`Filled`, `PartiallyFilled`, `Cancelled`, `Expired`)
are inferred from the SDK's TypeScript typing but **not directly
verified** at the time of writing. Please PR if you observe them.

`execution[].sold_amount` / `bought_amount` / `surplus_buy_amount` are
hex-prefixed bigint strings — parse with a base-0 int parser.

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

Value is `0xd8d = 3469` atomic units in the example. See
[fees.md](fees.md) for how to interpret this — spoiler: it works out
to ~0.99 bps (0.0099%) of the mid-price notional, flat.

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
