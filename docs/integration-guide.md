# Integration guide

Pragmatic notes on building a client — the things we'd have saved
time on if we had read this first.

## Setup checklist

Before your client makes a single API call:

1. **Confirm ERC-20 approval** of every token you plan to trade,
   granting the Permit2 contract allowance. See
   [permit2.md](permit2.md). If approval is missing, `/api/add_order`
   submissions succeed but settlement will revert.
2. **Hold gas reserve** for one-time `approve` calls. You do **not**
   need gas for ongoing trading — settlement gas is paid by Turbine's
   executor EOA.
3. **Have a reliable Ethereum RPC.** MEV Blocker
   (`https://rpc.mevblocker.io`) is free, has no aggressive rate
   limits on read methods, and keeps your balance/nonce queries
   private from front-runners. Avoid llamarpc / blastapi for
   high-frequency reads — we hit 429s during canary testing.
4. **Pin contract addresses.** Fetch `/api/config` at startup and
   compare every field against hard-coded pins in your config. Halt
   on mismatch.
5. **Pin SIWE fields.** `siweDomain` and `siweUri` are what you
   sign — if they changed on the server, your signatures would
   become cross-origin valid, which is bad. Pin them.

## Startup sequence

```
acquire flock (prevent dual instance)
       │
       ▼
load wallet (decrypt keystore)
       │
       ▼
open/replay durable journal
       │
       ▼
GET /api/status                 ← sanity ping
       │
       ▼
GET /api/config                 ← with pin verification (fail fast)
       │
       ▼
fetch live price from Binance   ← for equity baseline
       │
       ▼
read ERC-20 balances on-chain   ← USDC + WETH of wallet
read ERC-20 allowances on-chain ← to Permit2
read Permit2 executor balance   ← soft warning if < 0.01 ETH
       │
       ▼
compute initial_equity_usd      ← baseline for drawdown stop
       │
       ▼
enter main loop
```

## Main loop cadence

```
every tick (e.g. 15-30 s):
    refresh oracle (Binance mid)
    risk checks       ← drawdown, api failures, oracle staleness
    poll open orders  ← POST /api/order_states with all live hashes
    place missing slots according to strategy

every ~5 min:
    reconcile on-chain state (balances + ERC-20 allowances)
    executor balance probe

every 6 s (separate cadence if you want faster fill detection):
    poll /api/order_states again
```

Follow the front-end's pattern of **serializing concurrent polls**
with a mutex. Two overlapping `/api/order_states` calls on the same
hashes produce identical responses and waste quota.

## SIWE auth serialization

The nonce/verify pair is single-use. If your client runs multiple
threads and each re-authenticates independently when they see a
logged-out session, you will race and one of them will fail. Put a
mutex around the entire SIWE flow:

```python
def ensure_authenticated(self):
    with self._auth_lock:
        if self.is_authenticated():
            return
        self.authenticate()        # /nonce + /verify
        # Re-check so we can return the right address if something
        # flipped us out of the session immediately
        if not self.is_authenticated():
            raise AuthFailed()
```

## Order placement pattern

One fill-cycle for an intent looks like:

1. Build the `OrderIntent` (see [orders.md](orders.md)).
2. Read the current Permit2 nonce on-chain for
   `(wallet, sellToken, settler)`.
3. Build and sign the `PermitSingle` EIP-712 typed data.
4. Convert signature to `{r, s, yParity: bool, v}`.
5. POST `/api/add_order` with `{order, signedPermit}`.
6. On `{orderHash: "0x..."}`, start polling that hash via
   `/api/order_states`.
7. On `status = "Filled"` (TitleCase!), consider the order complete.
   On `"Cancelled"` or `"Expired"`, drop it and free the slot.
8. If you need to cancel early, POST `/api/cancel_order` with the
   hash.

The JS SDK template in `turbineClient.ts` follows this exactly, plus
handles smart-order callbacks and batch submissions. Our Python
reference in [examples/minimal_client.py](../examples/minimal_client.py)
implements the non-smart path.

## State tracking

Keep a local ledger that associates every submitted order with:

* `local_id` (a UUID you generate pre-submit, so you can recover from
  half-completed submits)
* `order_hash` (assigned by the server)
* `side` (buy/sell)
* `spread_bps` (what you asked for)
* `eth_price_usd` (mid at submit time)
* `expected_buy_atomic` (what you'd get at mid)
* `fee_atomic` (from `/api/order_fees`, for realized-yield
  computation)
* current `status` from polling
* `executed_sell_amount` / `executed_buy_amount` from the latest poll

Use that to compute realized PnL, fill rates per spread bucket, and
time-to-fill distributions. The fee field in particular is essential
for any honest yield attribution — see [fees.md](fees.md).

## Error handling

Every request can fail in one of:

* **HTTP 2xx** — success
* **HTTP 4xx with JSON body** — business error
  ```json
  {"code": "INPUT_VALIDATION_ERROR", "message": "..."}
  ```
* **HTTP 4xx with plain text body** — serde deserialization error
  ```
  Failed to deserialize the JSON body into the target type: ...
  ```
* **HTTP 5xx** — transient; retry with backoff
* **Network error** — same as 5xx

Wrap both JSON and plain-text responses so your error surface has
the raw body attached to the exception — it saves hours of guessing
when something goes wrong.

## Concurrency safety

If your architecture uses multiple threads:

1. **Serialize SIWE auth** — one mutex around the `/nonce → /verify`
   pair per client instance.
2. **Serialize order book mutations** — if multiple threads can
   submit, cancel, or update state, put a single mutex around the
   state manager.
3. **Don't share `requests.Session()` without care** — in practice
   it is thread-safe for concurrent requests, but you shouldn't rely
   on that for stateful flows. Hold the auth lock while touching
   cookies.

## Idempotency

Nothing on the Turbine API is idempotent by default. `/api/add_order`
sent twice will create two orders (with different hashes if you use
different salts, same hash if you replay the same intent).

Generate a random salt per submission and use a persistent journal
on your side so replays after a crash don't double-submit.

## Rate limits

We have not hit rate limits in any Alpha testing. If you run a
large production bot, start conservative (6-second poll interval per
the SDK) and ramp up only if needed.

## Minimum order size

**$30 USD**. Enforced server-side. Pricing in USD means you need to
convert your atomic amounts through a live mid-price at submit time
and leave buffer for price drift between build and submit. A safe
floor is ~$35 for a "small" order.

## Monitoring the executor

The Turbine executor EOA (`0x89c7…8890` at time of writing) is the
one who actually pays gas. If its balance approaches zero, your
orders may queue up without settling.

Passive approach: poll `eth_getBalance(executor)` every 5 minutes,
emit a warning below 0.01 ETH, **do not halt** (you do not control
it — halting your bot does nothing for the problem). Emit a louder
alert to your operator channel and wait for PropellerHeads to
top up.

## Cancel-on-shutdown pattern

On SIGTERM / clean shutdown, do:

```python
def shut_down(self):
    # Best-effort cancel of every live order
    for order_hash in self.live_order_hashes():
        try:
            self.client.cancel_order(order_hash)
        except TurbineClientError as exc:
            log.error("cancel_order failed: %s", exc)

    # ALWAYS re-check after cancel_all — some cancels may have failed
    still_live = self.still_live_order_hashes()
    if still_live:
        log.critical("SHUTDOWN WITH LIVE ORDERS: %s", still_live)
```

A bot that exits "cleanly" while leaving orders live on the venue
is a silent exposure bug. Log a CRITICAL so you notice.
