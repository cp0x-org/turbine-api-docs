# Contributing

Everything in this repository was verified against the live
production Turbine API on **2026-04-13**. Turbine is in Alpha and
things change without notice, so if you observe a drift we'd love a
PR.

## What we especially want

1. **Status enum transitions.** The documented `OrderStatus` set is
   `Active`, `Filled`, `PendingCancellation`, `Canceled`, `Invalid`,
   `Expired`, `Adding`, `PartiallyFilled`, `Unknown`. We have not
   directly observed every transition value live — if you capture a
   real `/api/order_states` response showing one of the rarer states,
   please PR with the exact payload.
2. **Fees.** `/api/order_fees` is the source of truth (it returns the
   fee in buy-token atomic units). Any older bps figure in
   [docs/fees.md](docs/fees.md) is a historical, unverified Alpha
   observation — re-run the probe against the current deployment and
   PR with what you measure.
3. **New endpoints.** If PropellerHeads ship official Turbine docs
   that contradict anything here, PR a correction.
4. **Error shapes.** We documented two observed error response
   formats — there may be more.
5. **Rate limits.** We have not hit any. If you do, headers +
   behaviour welcome.

## Process

* Open an issue first if you are planning a big change. Small
  corrections can go straight to a PR.
* Keep attribution (CC-BY-4.0): add yourself to a CONTRIBUTORS list
  in the PR description if you want credit.
* Prefer verified facts. If you're unsure, mark the claim with
  "**Unverified:**" so readers know.
* Do **not** include private keys, wallet addresses tied to real
  funds, or any secret data in PRs or examples.

## Running the probe against production

Minimal reproducer for the fee measurement:

```python
from examples.minimal_client import MinimalTurbineClient, OrderIntent, constant, random_salt, WETH, USDC
import os, time
from eth_account import Account

client = MinimalTurbineClient(Account.from_key(os.environ["WALLET_PRIVATE_KEY"]))
client.check_status()
client.fetch_config_and_pin()
client.authenticate()
now = int(time.time())
intent = OrderIntent(
    owner=client.account.address,
    sell_token=WETH, buy_token=USDC,
    sell_amount=int(0.016 * 10**18),
    min_buy_amount=33 * 10**6,
    # v0.114 replaced the scalar midPriceDelta with a spreadCurve.
    # constant(-10) is a flat curve == the old fixed -10 bps delta.
    spread_curve=constant(-10),
    start_time=now, end_time=now + 3600,
    salt=random_salt(),
)
print("fee:", client.quote_fee(intent))
```

`/api/order_fees` does not actually place an order — it is safe to
run for as many sizes / spread curves / sides as you want.
