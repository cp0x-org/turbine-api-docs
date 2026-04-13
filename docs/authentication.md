# Authentication — SIWE over cookies

Turbine uses **SIWE (Sign-In With Ethereum, [EIP-4361](https://eips.ethereum.org/EIPS/eip-4361))**
for authentication. Session state lives in an HTTP cookie set by the
server; subsequent requests to authenticated endpoints must be sent
with that cookie (`credentials: "include"` in the browser, a
persistent `requests.Session()` in Python).

## Flow

```
        Client                                Server
        ------                                ------
 1.  POST /api/nonce         ───────────►
                             ◄───────────  200 OK, body = "<nonce string>"
                                           Set-Cookie: <session>

 2.  build SIWE message
     personal_sign(message)

 3.  POST /api/verify        ───────────►
       { message, signature }
                             ◄───────────  200 OK
                                           Set-Cookie: <authenticated session>

 4.  GET /api/me             ───────────►
                             ◄───────────  { authenticated: true, address: "0x..." }
```

## Step 1: `POST /api/nonce`

Empty body. Response is a JSON string (not an object):

```
"b3a9f0e1c8d7..."
```

Save the nonce for the next step.

## Step 2: build the SIWE message

Construct the message *exactly* per the EIP-4361 template.
`app.turbine.exchange` uses `viem`'s `createSiweMessage` which emits
the following shape:

```
app.turbine.exchange wants you to sign in with your Ethereum account:
0x15cCf087670A46e54d71Bd1DE429dDE0c372D17f

Sign in to Turbine with your Ethereum wallet

URI: https://api.turbine.exchange/api
Version: 1
Chain ID: 1
Nonce: <nonce from step 1>
Issued At: 2026-04-13T00:00:00.000Z
```

Fields:

* **Domain** (line 1): must be `app.turbine.exchange`. Pin this.
* **Address** (line 2): EIP-55 checksummed wallet address.
* **Statement** (line 4): must be exactly
  `"Sign in to Turbine with your Ethereum wallet"`.
* **URI**: `https://api.turbine.exchange/api`. Pin this.
* **Version**: `"1"`.
* **Chain ID**: `1` for Ethereum mainnet.
* **Nonce**: the string returned by `/api/nonce`.
* **Issued At**: ISO-8601 UTC timestamp with millisecond precision.
  Required by EIP-4361.

## Step 3: sign with `personal_sign`

Use **EIP-191 / `personal_sign`**, *not* EIP-712. In `eth_account`:

```python
from eth_account.messages import encode_defunct

encoded = encode_defunct(text=siwe_message)
signed = account.sign_message(encoded)
sig_bytes = signed.signature  # 65 bytes: r (32) + s (32) + v (1)
```

In a browser wallet that means `personal_sign` / `eth_sign` with
string input, not `eth_signTypedData_v4`.

## Step 4: `POST /api/verify`

Body is a JSON object:

```json
{
  "message": "<the SIWE string exactly as signed>",
  "signature": {
    "r": "0xab…",
    "s": "0xcd…",
    "yParity": "0x0",
    "v": "0x1b"
  }
}
```

Signature field details:

* `r`, `s` — hex-encoded 32-byte components, `0x`-prefixed.
* `v` — hex-encoded recovery id as `"0x1b"` (27) or `"0x1c"` (28).
* `yParity` — **hex string** `"0x0"` (for `v == 27`) or `"0x1"`
  (for `v == 28`) on this endpoint.

> **Wire-format pitfall.** The same field is sent differently on
> `/api/verify` and on `/api/add_order`. Here on `/verify` it is a
> **string**. On `/add_order` inside `signedPermit.signature` it is a
> **JSON boolean** `true` / `false`. Sending the bool to `/verify`
> or the string to `/add_order` produces a 422. See
> [wire-format.md](wire-format.md) for the full analysis.

Successful `/verify` returns HTTP 200 and sets the authenticated
session cookie. The body is not used.

## Step 5: confirm with `GET /api/me`

```json
{
  "authenticated": true,
  "address": "0x15ccf087670a46e54d71bd1de429dde0c372d17f"
}
```

The address comes back **lowercased**; do a case-insensitive
comparison against your wallet.

## Session lifetime

We have not measured the cookie expiration window. The JS SDK
transparently re-authenticates whenever `/me` returns
`authenticated: false`, so a safe pattern is:

1. Try the request.
2. On HTTP 401 (or `/me` saying false), re-run the SIWE flow.
3. Retry the original request with the fresh cookie.

Serialize the re-auth flow with a mutex if you have multiple threads
sharing a client — Turbine's `/nonce` + `/verify` pair is single-use
per nonce, so two concurrent auth attempts will race.

## Logout

```
POST /api/logout     (no body)
```

Clears the session server-side. Your cookie jar should also clear.
