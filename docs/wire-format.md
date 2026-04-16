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
* `/api/order_states` execution entries → `sold_amount`,
  `bought_amount`, `surplus_buy_amount`

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

Other values we expect from the SDK's TypeScript typing but have
**not directly observed** yet:

* `"Filled"` — fully executed
* `"PartiallyFilled"` — some execution history, still active
* `"Cancelled"` — user or server cancelled

If you observe any of these in the wild, please PR to update this
file.

## `midPriceDelta` type: integer, not string

Unlike most bigint fields in `OrderIntent`, `midPriceDelta` is a
**JSON integer**, not a string. It fits in an `int16` (`-10000..+10000`)
and the SDK treats it as a number.

```jsonc
{
  "midPriceDelta": -15,   // integer, not "-15"
  ...
}
```

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
