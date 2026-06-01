# Wire format — the non-obvious bits

If you are building a client from scratch, these are the details
that cost real time to discover and that aren't visible from a
cursory SDK read. Every item below was validated against the live
production API on 2026-04-13.

## Bigint serialization: hex on the way down

Every bigint field in Turbine **responses** is serialized as a hex
string with an `0x` prefix, via the SDK's `bigIntReplacer`:

```javascript
function bigIntReplacer(_key, value) {
    if (typeof value === "bigint") {
        return `0x${value.toString(16)}`;
    }
    return value;
}
```

Affected fields we've verified:

* `/api/order_fees` → returns strings like `"0x1ef"`,
  `"0x294f9e980"`
* `/api/order_states` execution entries → the per-execution amounts
  (sold / bought / surplus-bought). As of v0.114 these may appear in
  **camelCase** (`soldAmount`, `boughtAmount`, `surplusBoughtAmount`) in
  addition to the legacy **snake_case** (`sold_amount`, `bought_amount`,
  `surplus_buy_amount`). We have not confirmed which casing production
  emits in every code path, so parse tolerantly — accept both spellings.

Python `int("0x1ef")` without a base raises `ValueError` because
`int()` defaults to base 10. Parse with **base 0**:

```python
def parse_bigint(value):
    if isinstance(value, bool):
        raise ValueError("bool is not a bigint")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)   # auto-detect 0x / 0o / decimal
    raise ValueError(f"unexpected bigint type: {type(value).__name__}")
```

**Inputs** (`OrderIntent.sellAmount`, `OrderIntent.minBuyAmount`,
`PermitSingle.amount`, `PermitSingle.sigDeadline`) can be either
decimal strings (`"1000000000000000"`) or hex strings (`"0xde0b6b3a7640000"`).
The SDK validation in `validation.ts` uses JavaScript's `BigInt(value)`
which accepts both forms. We verified decimal inputs are accepted by
production. If you want to match the SDK exactly, use hex on the way up
as well.

## `yParity`: boolean on `/add_order`, string on `/verify`

The structured signature object has this shape:

```jsonc
{
  "r":       "0x...",   // 32-byte hex
  "s":       "0x...",   // 32-byte hex
  "yParity": ???,       // depends on endpoint
  "v":       "0x1b"     // 0x1b (27) or 0x1c (28)
}
```

`yParity` is serialized differently on two endpoints:

| Endpoint | `yParity` type | Example |
|---|---|---|
| `POST /api/verify` (SIWE) | **hex string** | `"0x0"` or `"0x1"` |
| `POST /api/add_order` → `signedPermit.signature` | **JSON boolean** | `true` or `false` |

Sending a string to `/add_order` produces:

```
HTTP 422
Failed to deserialize the JSON body into the target type:
signedPermit.signature.yParity: invalid type: string "0x0", expected a boolean at line 1 column 698
```

Sending a bool to `/verify` produces the mirror error. The two
endpoints are deserialized by different Rust structs on the backend;
the two conventions happen to coexist historically.

**Recommendation:** store `yParity` internally as a Python `bool`
(canonical form). Serialize differently per call site:

```python
def to_permit2_json(self):
    return {"r": self.r, "s": self.s, "yParity": self.y_parity, "v": self.v}

def to_siwe_json(self):
    return {
        "r": self.r, "s": self.s,
        "yParity": "0x1" if self.y_parity else "0x0",
        "v": self.v,
    }
```

Do **not** keep the string form as canonical — when you want to
compare or compute, bool is what's actually useful.

The mapping from ECDSA `v` to `yParity`:

| `v` (decimal) | `yParity` (bool) | `yParity` (string) |
|---|---|---|
| 27 | `false` | `"0x0"` |
| 28 | `true` | `"0x1"` |

## `status` field casing

`/api/order_states` returns the order `status` as **TitleCase**:

```
"status": "Active"
```

If you wrote your state machine guessing `"active"` or `"open"` or
`"RESTING"`, it will silently not transition. Normalize with
`.lower()` on read, or match case-exact.

Verified status values:

* `"Active"` — resting in the book, no fills
* `"Expired"` — past `endTime`, auto-expired by the solver

The full `OrderStatus` enum from the v0.114 SDK typing
(`types/orders.ts`) is:

* `"Active"` — resting in the book
* `"Filled"` — fully executed
* `"PendingCancellation"` — cancellation requested, not yet final
* `"Canceled"` — cancelled (note: **one** `l`, US spelling)
* `"Invalid"` — order rejected/invalid
* `"Expired"` — past `endTime`
* `"Adding"` — being added, pre-`Active`
* `"PartiallyFilled"` — some execution history, still active
* `"Unknown"` — fallback

Only `"Active"` and `"Expired"` are **directly observed**; the rest come
from the SDK typing and have **not all been seen in the wild**. Match
case-exact (`"Canceled"`, not `"Cancelled"`). If you observe any of the
unobserved values, please PR to update this file.

## `spreadCurve` type: object of JSON integers

As of v0.114, the scalar `midPriceDelta` field is **gone**. Orders now
carry `spreadCurve`, a delta curve over the order's time window. Every
numeric field inside it is a **JSON integer**, not a string — unlike the
bigint fields in `OrderIntent`, these are plain numbers the SDK treats as
such.

Request shape:

```jsonc
{
  "spreadCurve": {
    "startDeltaBps": -15,   // integer; delta at windowBps=0 (startTime)
    "endDeltaBps":   -15,   // integer; delta at windowBps=10000 (endTime)
    "points": [             // interior knots, may be empty
      { "windowBps": 5000, "deltaBps": -8 }
    ]
  },
  ...
}
```

* `windowBps` is **normalized order-window time**: `0` = `startTime`,
  `10000` = `endTime`. Interior knots live in `[1, 9999]`
  (`MIN_WINDOW_BPS=1`, `MAX_WINDOW_BPS=9999`).
* `deltaBps` is **signed**, 1 unit = 1 basis point (0.01%), domain
  `[MIN_DELTA_BPS=-10000, MAX_DELTA_BPS=9999]`. Negative = maker price
  better than mid.
* The effective delta at any time `now` is the piecewise-linear
  interpolation between the surrounding knots.
* `points` is capped at `MAX_SPREAD_CURVE_POINTS = 1024` (an SDK DoS
  guard; the backend enforces a tighter bound based on order duration and
  block interval).

The two values that previously would have been a single `midPriceDelta`
are now expressed via the SDK's `constant(deltaBps)` builder, which emits
a flat curve — `{ startDeltaBps: d, endDeltaBps: d, points: [] }` — that
reproduces the old fixed-delta behavior exactly. A second builder,
`auto({ fastSpreadBps, deltaBps?, yoloBps? })`, emits a 4-knot ramp (the
"auto-spread" order type). The curve-construction semantics are
documented elsewhere; from a wire perspective both builders just produce
the `spreadCurve` object above.

### Resolved curve in responses

When `spreadCurve` comes back inside an order's `orderDetails` (read
path), the backend resolves it to absolute Unix seconds: `startSecs`,
`endSecs`, plus `startDeltaBps`/`endDeltaBps` and a `points` array whose
entries use `timeSecs` (absolute seconds) instead of the request's
`windowBps`. We have **not exhaustively verified** every field of the
resolved form against production — if you observe a divergence, please PR
to update this file.

## `sellAmount` / `minBuyAmount` type: string

Both are atomic-units bigints wrapped in JSON strings:

```jsonc
{
  "sellAmount":   "16000000000000000",
  "minBuyAmount": "33000000",
  ...
}
```

Decimal strings are accepted by the server. The SDK generates them
via `value.toString()` (Python equivalent: `str(int_value)`).

## `startTime` / `endTime` type: string

These are Unix seconds as **strings**, not integers:

```jsonc
{
  "startTime": "1776048000",
  "endTime":   "1776051600",
  ...
}
```

The validation accepts both string and number forms if you decode
them as BigInts, but the SDK emits strings, and the frontend does
too. Stick with strings for compatibility.

## `partialFill` type: bool

Plain JSON boolean. Front-end always emits `true`:

```jsonc
{
  "partialFill": true,
  ...
}
```

## `callData` and `callDataTarget`

For non-smart orders: `callData = "0x"` (literal two-character hex
string) and `callDataTarget = "0x0000000000000000000000000000000000000000"`
(the zero address). Both are **strings**; the zero address must be
full 20-byte hex, not `"0x"`.

## `salt`

A 32-byte random value, `0x`-prefixed hex string (66 chars total).
Not a bigint — it is a bytes32 value. Keep it as a string:

```python
import secrets
salt = "0x" + secrets.token_bytes(32).hex()
```

## Response bodies on errors

Two distinct shapes in the wild:

**Business validation errors (HTTP 400):**

```json
{
  "code": "INPUT_VALIDATION_ERROR",
  "message": "Sell amount 9100000000000000 is worth ~$19.971538 which is less than $30"
}
```

**Serde deserialization errors (HTTP 422):**

```
Failed to deserialize the JSON body into the target type:
signedPermit.signature.yParity: invalid type: string "0x0", expected a boolean at line 1 column 698
```

Note the second form is **plain text**, not JSON. A robust client
should:

```python
try:
    body = response.json()
except ValueError:
    body = response.text
```

and surface both.

## Cookies and session

`/api/verify` sets an HTTP cookie that carries the session. Every
authenticated endpoint must be called with `credentials: "include"`
(browser) or a persistent `requests.Session()` (Python). Missing the
cookie returns an auth failure.

The exact cookie name is implementation-detail; we don't need to
parse it, just pass it through.

## Address format

All addresses in requests are **EIP-55 checksummed** hex strings.
`Web3.to_checksum_address` in Python. Lowercased or uppercased
addresses may be accepted by lenient validators but the SDK emits
checksummed.

In responses (e.g. the `/api/me` `address` field), the server
sometimes returns lowercase. Compare case-insensitively on your side.

## Empty-body responses

`POST /api/verify` and `POST /api/logout` return HTTP 200 with an
empty body on success. Do not try to parse JSON — check status code
only.
