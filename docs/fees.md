# Platform fees

Turbine charges a **platform fee** on every filled order. If you are
building any kind of active strategy — especially a market maker
that earns on narrow spreads — you **must** account for this fee
when sizing positions and computing expected yield. It is not
documented anywhere on docs.propellerheads.xyz at the time of
writing; the only signal in the app UI is a tooltip that says
`"Includes a platform fee of 0.01%"` rounded to two decimals.

**The authoritative source of truth for the fee is the
`POST /api/order_fees` endpoint** (see below). It returns the exact
platform fee the server would charge for a given order, denominated
in buy-token atomic units. Do not hardcode a fee rate — quote it per
order before submitting.

> **Historical note (unverified for v0.114).** On an earlier Turbine
> deployment, the fee was empirically observed to be approximately
> **0.99 bps (0.0099%) of the mid-price notional**, flat regardless of
> size, side, or the spread the order requested. That observation was
> made during the Turbine Alpha private beta and has **not** been
> re-verified against the current `v0.114.1` deployment. Treat any
> fixed fee number as historical until you reproduce it yourself via
> `/api/order_fees`.

This page documents how to query the fee from the API and how it
interacts with the order's spread.

## Querying the fee

`POST /api/order_fees` is a quote-only endpoint: you send a full
`OrderIntent` (the raw order body, without `signedPermit`) and the
server returns the platform fee it would charge, denominated in
**buy-token atomic units**, as a hex-prefixed bigint string.

This is the canonical way to obtain the fee for any prospective
order. Because the response is in buy-token atomic units, divide by
the buy token's decimals to convert to a human-readable amount, and
optionally divide by `minBuyAmount` to express it as a ratio of the
order notional.

### Example response shape

> The response is a single fee value in buy-token atomic units as a
> hex-prefixed bigint string. The exact JSON envelope is not asserted
> here; inspect a live `/api/order_fees` response for the current
> deployment. If you observe a different shape, please open a PR.

### Historical measurement (Alpha beta — unverified for v0.114)

The numbers below were collected against the **old** deployment on
2026-04-13 by sweeping `/api/order_fees` over a range of sizes,
spreads, and both sides (sell WETH→USDC, buy USDC→WETH). They are
retained for illustration of the fee's *structure*, not as a current
rate. Re-run your own sweep before relying on any of them.

ETH mid-price at the time of that sweep: $2193.45.

#### Selected sell-side results (WETH → USDC, buy token USDC)

| Size | `sellAmount` (WETH atomic) | `minBuyAmount` (USDC atomic) | Fee (USDC atomic) | Fee / size_usd |
|---|---|---|---|---|
| $5    | 2,279,000,000,000,000 | 4,500,000   | 495     | 0.0099% |
| $20   | 9,118,000,000,000,000 | 18,000,000  | 1,980   | 0.0099% |
| $35   | 15,957,000,000,000,000 | 31,500,000 | 3,465   | 0.0099% |
| $100  | 45,590,000,000,000,000 | 90,000,000 | 9,900   | 0.0099% |
| $200  | 91,180,000,000,000,000 | 180,000,000 | 19,800  | 0.0099% |
| $500  | 227,950,000,000,000,000 | 450,000,000 | 49,500  | 0.0099% |
| $1000 | 455,900,000,000,000,000 | 900,000,000 | 99,000  | 0.0099% |

#### Selected buy-side results (USDC → WETH, buy token WETH)

| Size | `sellAmount` (USDC atomic) | `minBuyAmount` (WETH atomic) | Fee (WETH atomic) | Fee / size_usd |
|---|---|---|---|---|
| $5    | 5,000,000     | 2,051,000,000,000,000   | 225,671,886,753     | 0.0099% |
| $35   | 35,000,000    | 14,357,000,000,000,000  | 1,579,703,207,275   | 0.0099% |
| $200  | 200,000,000   | 82,040,000,000,000,000  | 9,026,875,470,148   | 0.0099% |
| $1000 | 1,000,000,000 | 410,200,000,000,000,000 | 45,134,377,350,748  | 0.0099% |

### Observations (from the historical sweep)

These properties held on the old deployment and may or may not still
hold; verify with your own `/api/order_fees` calls.

1. **Fee was linear in swap size.** Sell $5 → 495 atomic USDC,
   sell $1000 → 99,000 atomic USDC. Slope = 99 atomic per $1.
2. **Fee was independent of the requested spread.** Sweeping the
   order's delta returned the exact same fee for a given size. The
   spread an order requests is a filter on execution price, not an
   input to fee calculation.
3. **Fee was symmetric across sides.** Selling USDC vs selling WETH
   yielded the same 0.0099% ratio.
4. **Flat ~0.99 bps of the mid-price expected value.** Every row was
   within 0.001 bps of the same number.

## Interpretation: fee vs. requested spread

In v0.114, an order no longer carries a single fixed `midPriceDelta`
scalar. Instead it carries a `spreadCurve` — a piecewise-linear curve
of signed `deltaBps` values over the order's normalized time window
(see the orders documentation). Each `deltaBps` is in basis points
(1 unit = 1 bp = 0.01%); negative values are maker-favorable
("earn"), positive values are pay-to-fill.

The platform fee is independent of the curve you request. It is
**deducted from the order creator's realized price improvement**. The
app frontend computes a fee ratio from the `/api/order_fees` value
divided by `minBuyAmount`, then subtracts it from the order's
effective delta to produce the displayed "Your spread" number.

Concretely, if your order is effectively running at `-10 bps`
(maker-favorable), the realized spread after the platform fee is
roughly `10 - ~1 = ~9 bps` (using the historical ~0.99 bps figure as
an illustration; quote the real fee per order). For a pay-to-fill
order at `+50 bps`, the same fee structure widens your effective cost
from 50 bps to ~51 bps.

## Impact for narrow-spread strategies

If a strategy earns on a small maker-favorable delta, the platform
fee is a fixed subtraction from each fill's realized edge. Using the
historical ~1 bp figure purely for illustration of the *shape* of the
impact (not as a current rate):

| Effective spread | Illustrative fee | Net realized edge | Fee as % of spread |
|---|---|---|---|
|  5 bps | ~1 bp | ~4 bps | ~20% cut |
| 10 bps | ~1 bp | ~9 bps | ~10% cut |
| 15 bps | ~1 bp | ~14 bps | ~6.7% cut |
| 20 bps | ~1 bp | ~19 bps | ~5.0% cut |
| 30 bps | ~1 bp | ~29 bps | ~3.3% cut |
| 50 bps | ~1 bp | ~49 bps | ~2.0% cut |

The takeaway is structural, not numeric: a fixed per-fill fee
disproportionately hurts the narrowest-spread buckets. Below a few
bps of effective spread, the fee consumes a large fraction of the
edge, so such buckets need a very high fill rate to remain profitable
in expectation. Quote the live fee from `/api/order_fees` and plug
your own numbers into this calculation.

## Estimating the fee offline

You generally do not need to call `/api/order_fees` for every
prospective order for UI display or pre-trade feasibility checks — if
you have characterized the fee's structure on the current deployment,
a linear estimate is accurate to within rounding noise. **Always use
`/api/order_fees` for the authoritative value before actually
submitting**, and re-derive the rate below from your own `order_fees`
sweep rather than assuming the historical constant still holds.

```python
def estimate_fee_atomic(sell_amount_atomic, sell_token_decimals,
                        mid_price_usd_per_eth, buy_token_decimals,
                        fee_bps):
    """Returns the estimated platform fee in buy-token atomic units.

    `fee_bps` must be derived from your own /api/order_fees sweep on
    the current deployment — do not assume a fixed rate.
    """
    # Convert the sell amount to USD at the mid
    sell_usd = sell_amount_atomic / (10 ** sell_token_decimals)
    if sell_token_decimals != 6:          # assume non-USDC is ETH-priced
        sell_usd *= mid_price_usd_per_eth
    # Fee in USD
    fee_usd = sell_usd * fee_bps / 10_000
    # Fee in buy-token atomic units
    if buy_token_decimals == 6:           # USDC
        return int(fee_usd * 10 ** 6)
    else:                                 # WETH
        return int((fee_usd / mid_price_usd_per_eth) * 10 ** 18)
```

## Accounting for the fee in yield analysis

When analysing realized yield from execution records, subtract the
platform fee (the exact value from `/api/order_fees` for the quoted
intent, or your measured rate) from whatever raw spread the execution
appears to show. Otherwise mean realized yield is over-reported.

For exact accounting, capture the fee in your fill-level records at
submit time — grab it from `/api/order_fees` while building the order
— and compute `realized - fee` at analysis time.

## Caveats

* Any fixed fee figure on this page (e.g. ~0.99 bps) was measured
  during the **Turbine Alpha private beta on an earlier deployment**
  and is **unverified for `v0.114.1`**. PropellerHeads can change the
  fee at any time. Query `/api/order_fees` against the current
  deployment before making production decisions.
* It is not verified whether the fee differs for LP
  (`add_liquidity`) or for smart orders (non-empty `callData`). The
  historical measurement covered plain-intent orders only.
* The fee is deducted from the order creator's side. For a maker
  sending passive orders and a taker sending orders that match
  against them, both sides may pay — a matched-fill experiment to
  confirm this has not been documented. If you run one, please PR the
  results.
