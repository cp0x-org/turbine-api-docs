# Platform fees

Turbine charges a **platform fee** on every filled order. If you are
building any kind of active strategy — especially a market maker
that earns on narrow spreads — you **must** account for this fee
when sizing positions and computing expected yield. It is not
documented anywhere on docs.propellerheads.xyz at the time of
writing; the only signal in the app UI is a tooltip that says
`"Includes a platform fee of 0.01%"` rounded to two decimals.

**The actual fee: ~0.99 bps (0.0099%) of the mid-price notional, flat
regardless of size, side, or `midPriceDelta`.**

This page documents how we measured that and how it interacts with
spread.

## Measuring the fee

`POST /api/order_fees` is a quote-only endpoint: you send a full
`OrderIntent` (without `signedPermit`) and the server returns the
platform fee it would charge, denominated in **buy-token atomic
units**, as a hex-prefixed bigint string.

We ran this against production on 2026-04-13 with a sweep over:

* 7 sizes: $5, $20, $35, $100, $200, $500, $1000
* 4 deltas: `-5`, `-10`, `-20`, `-30` bps
* Both sides (sell WETH→USDC, buy USDC→WETH)

ETH mid-price at the time: $2193.45.

### Selected sell-side results (WETH → USDC, buy token USDC)

| Size | `sellAmount` (WETH atomic) | `minBuyAmount` (USDC atomic) | `delta_bps` | Fee (USDC atomic) | Fee / size_usd |
|---|---|---|---|---|---|
| $5    | 2,279,000,000,000,000 | 4,500,000   | −10 | 495     | 0.0099% |
| $20   | 9,118,000,000,000,000 | 18,000,000  | −10 | 1,980   | 0.0099% |
| $35   | 15,957,000,000,000,000 | 31,500,000 | −10 | 3,465   | 0.0099% |
| $100  | 45,590,000,000,000,000 | 90,000,000 | −10 | 9,900   | 0.0099% |
| $200  | 91,180,000,000,000,000 | 180,000,000 | −10 | 19,800  | 0.0099% |
| $500  | 227,950,000,000,000,000 | 450,000,000 | −10 | 49,500  | 0.0099% |
| $1000 | 455,900,000,000,000,000 | 900,000,000 | −10 | 99,000  | 0.0099% |

### Selected buy-side results (USDC → WETH, buy token WETH)

| Size | `sellAmount` (USDC atomic) | `minBuyAmount` (WETH atomic) | Fee (WETH atomic) | Fee / size_usd |
|---|---|---|---|---|
| $5    | 5,000,000     | 2,051,000,000,000,000   | 225,671,886,753     | 0.0099% |
| $35   | 35,000,000    | 14,357,000,000,000,000  | 1,579,703,207,275   | 0.0099% |
| $200  | 200,000,000   | 82,040,000,000,000,000  | 9,026,875,470,148   | 0.0099% |
| $1000 | 1,000,000,000 | 410,200,000,000,000,000 | 45,134,377,350,748  | 0.0099% |

### Observations

1. **Fee is linear in swap size.** Sell $5 → 495 atomic USDC,
   sell $1000 → 99,000 atomic USDC. Slope = 99 atomic per $1.
2. **Fee is independent of `midPriceDelta`.** All four tested delta
   values (−5, −10, −20, −30) return the exact same fee for a given
   size. The `midPriceDelta` is a pure filter on execution price, not
   an input to fee calculation.
3. **Fee is symmetric across sides.** Selling USDC vs selling WETH
   yields the same 0.0099% ratio.
4. **Flat 0.99 bps of the mid-price expected value.** Every row is
   within 0.001 bps of the same number.

## Interpretation

The frontend code in `TurbineOptions.tsx` computes:

```javascript
const feePercent = new Decimal(fee.toString())
    .div(intent.minBuyAmount)
    .times(100);
```

and then **subtracts** `feePercent` from the user's
`midPriceDelta.deltaPercent` to produce the displayed "Your spread"
number:

```javascript
const spreadPercent = feePercent !== undefined
    ? turbineOptions.delta.deltaPercent.minus(feeRatio)
    : turbineOptions.delta.deltaPercent;
```

i.e., the platform fee is **deducted from the order creator's
realized price improvement**. For a market maker setting
`midPriceDelta = -10 bps`, the actual realized spread is
`10 - ~1 = ~9 bps`. For a taker with `midPriceDelta = +50 bps`, the
fee is the same structure — it widens your effective cost from 50
bps to ~51 bps.

## Impact table for market making

If your strategy sets `midPriceDelta = -X bps` (passive MM / earn
side), your **net realized edge per fill** is approximately:

| Set spread `X` | Platform fee | Net realized edge | Fee as % of spread |
|---|---|---|---|
|  5 bps | 1 bp | **~4 bps** | 20% cut |
| 10 bps | 1 bp | **~9 bps** | 10% cut |
| 15 bps | 1 bp | ~14 bps | 6.7% cut |
| 20 bps | 1 bp | ~19 bps | 5.0% cut |
| 30 bps | 1 bp | ~29 bps | 3.3% cut |
| 50 bps | 1 bp | ~49 bps | 2.0% cut |

Small-spread strategies are disproportionately hurt by the fee.
Below ~5 bps the fee eats so much of the edge that unless fill rate
is extremely high, the bucket is unprofitable in expectation.

## Computing the fee locally

Because the fee is a simple linear function of size, you don't have
to call `/api/order_fees` for every prospective order. An offline
estimate is accurate to within rounding noise:

```python
def estimate_fee_atomic(sell_amount_atomic, sell_token_decimals,
                       mid_price_usd_per_eth, buy_token_decimals):
    """Returns the estimated platform fee in buy-token atomic units."""
    FEE_BPS = 0.99          # 0.0099%
    # Convert the sell amount to USD at the mid
    sell_usd = sell_amount_atomic / (10 ** sell_token_decimals)
    if sell_token_decimals != 6:          # assume non-USDC is ETH-priced
        sell_usd *= mid_price_usd_per_eth
    # Fee in USD
    fee_usd = sell_usd * FEE_BPS / 10_000
    # Fee in buy-token atomic units
    if buy_token_decimals == 6:           # USDC
        return int(fee_usd * 10 ** 6)
    else:                                 # WETH
        return int((fee_usd / mid_price_usd_per_eth) * 10 ** 18)
```

Use this for UI display and pre-trade feasibility checks; use
`/api/order_fees` for the authoritative value before actually
submitting.

## Post-run yield analysis

When analysing realized yield from a `fills.csv`, **subtract 1 bp**
(or the exact fee for the quoted intent) from whatever raw
spread the execution appears to show. Otherwise your "mean realized
yield" will be over-reported.

A simple correction:

```python
# In your bucket-level analysis:
net_yield_bps = raw_realized_yield_bps - 0.99
```

If you want to be exact, include the fee in your fill-level records
at submit time (grab it from `/api/order_fees` during the build
phase) and compute `realized - fee` at analysis time.

## Caveats

* The 0.99 bps figure was measured **2026-04-13 during Turbine Alpha
  private beta**. PropellerHeads can change this at any time. Re-run
  the probe against `/api/order_fees` before making production
  decisions.
* We have not verified whether the fee differs for LP (`add_liquidity`)
  or for smart orders (non-empty `callData`). The measurement above
  covers plain-intent orders only.
* The fee is deducted from the order creator's side. For an MM
  sending passive orders and a retail user sending taker orders that
  match against them, both sides may pay — we have not conducted a
  matched-fill experiment to verify. If you run one, please PR the
  results.
