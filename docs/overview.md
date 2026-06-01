# Turbine overview

**Turbine** is an intent-based decentralized exchange built by
[PropellerHeads](https://propellerheads.xyz). It is described by the
team as "**the first dark solver**" — an off-chain orderbook + solver
running inside a confidential computing environment (Phala dstack /
TDX) that matches user intents against internal LPs, peer orders,
and fallback AMM liquidity.

This page captures what an integrator needs to know; it does not
duplicate PropellerHeads' own marketing. For that see
[propellerheads.xyz](https://propellerheads.xyz) and
[docs.propellerheads.xyz](https://docs.propellerheads.xyz).

## Core idea

A user signs an **intent** off-chain that expresses *"I want to trade
up to X of token A for at least Y of token B, within price tolerance Δ,
expiring at time T"*. Turbine's TEE-resident solver takes that intent
and decides how and when to execute it. Users do not submit on-chain
transactions themselves; they only sign messages.

The settlement — actually moving the tokens on-chain — is performed
by a dedicated **executor wallet** that PropellerHeads operates and
funds with ETH for gas. From a user perspective this means:

* **You sign but you don't pay gas.** Settlement gas is paid by the
  executor, not by your wallet. You only pay the real trade cost and
  the platform fee (see [fees.md](fees.md)).
* **You never broadcast a transaction.** Turbine handles all
  on-chain mechanics.

## What you actually control

The user — or your client — controls three things per order:

1. **Token pair, amount, and direction** — what you want to trade.
2. **`spreadCurve`** — your price tolerance relative to the current
   mid-price, expressed as a *curve of deltas over the order's time
   window* rather than a single number. Each delta is signed basis
   points: negative means "I want better than mid" (market maker /
   earn side), positive means "I tolerate worse than mid" (taker /
   fast). A delta's range is `-10000` to `+9999` (`MIN_DELTA_BPS` ..
   `MAX_DELTA_BPS`). See below and [orders.md](orders.md) for the
   curve shape.
3. **Expiry** — `start_time` / `end_time` Unix seconds. Orders
   typically last up to an hour.

Everything else (matching, order book position, execution timing,
gas optimisation) is the solver's problem.

### From a fixed delta to a spread curve

Earlier deployments carried a single scalar `midPriceDelta` (one fixed
delta in bps for the whole order). That field is **gone**. An order now
carries a `spreadCurve`: a piecewise-linear function of *normalised
window time* (`windowBps`, where `0` = `startTime` and `10000` =
`endTime`), evaluated at the moment a fill is considered. This lets the
delta you're willing to accept change as the order ages, so **limit
orders no longer have to go stale** — you can let an order start
maker-favourable and become more fill-friendly toward expiry instead of
resting unfilled.

Two common shapes (the SDK ships builders for both):

* **Constant** — a flat curve where the delta is the same across the
  whole window. This reproduces the old fixed-`midPriceDelta`
  behaviour: pick one delta and hold it.
* **Auto (auto-spread)** — a ramp that starts maker-favourable and
  rises to a positive (pay-to-fill) delta by the end of the window, so
  the order is designed to reliably settle before it expires. This is
  no longer a pure passive-maker order; it trades a worse end-of-window
  price for a higher fill probability.

See [orders.md](orders.md) for the exact `spreadCurve` request shape,
the curve bounds, and the constant/auto builders.

## The three-contract on-chain surface

Despite being an off-chain solver, Turbine has a small on-chain
footprint you will interact with when setting up or monitoring
positions:

| Contract | Role |
|---|---|
| **Permit2** (`0x0000…3ac78BA3`) | Standard Uniswap Permit2. You approve this at the ERC-20 level, then sign per-order `PermitSingle` messages off-chain. Executor calls `transferFrom` through Permit2 at settlement time. |
| **Turbine Settler** (`0xbb3e…73f8a`) | The contract that receives the final `settle(...)` call from the executor. This is the `spender` in your signed `PermitSingle`. |
| **Turbine Signer / Executor** (`0x89c7…8890`) | The EOA that pays gas and submits settlement transactions. Monitor its balance if you are market-making — if it goes dark, your orders stop settling. |
| **LP Hook / Router / Pool Manager** | Only relevant if you interact with Turbine's Uniswap-v4-style LP surface. Not needed for plain intents. |

See [permit2.md](permit2.md) for the exact ERC-20 approval and signing
flow, and [api-reference.md](api-reference.md) for the
`GET /api/config` endpoint that returns these addresses (and which you
should pin).

`GET /api/config` also returns the **supported token list**, which has
grown substantially (now on the order of a few hundred tokens), each
with CEX oracle mappings (binance / bingx / bitget / coinbase / kraken
/ kucoin / okx). Do not hardcode the list — read it from the config
endpoint. The two tokens used throughout these docs as examples are
USDC (`0xA0b8…eB48`) and WETH (`0xC02a…56Cc2`).

## The executor (operational reality)

The executor wallet is a **single EOA** that signs and submits every
Turbine settlement. A few operational characteristics are worth
knowing:

* Its ETH balance tends to be kept low and topped up
  **just-in-time** — PropellerHeads funds it shortly before they need
  to settle, rather than holding a large standing balance.
* Settlement transactions batch multiple orders into one tx where
  possible; the on-chain method selector is `0xfdcc8090`
  (`settle` on the Turbine Settler contract).

If you run an automated client, treat the executor balance as a soft
signal only — it can be useful to warn when it drops very low (e.g.
below ~0.01 ETH), but do not halt your own logic on it, since you do
not control it and just-in-time funding makes a low balance normal.

## Alpha caveats

* **Private-beta volume.** Fill probability at competitive spreads
  (e.g. 5–10 bps) is low; expect individual orders to rest tens of
  minutes to hours.
* **Single supported chain.** Ethereum mainnet only.
* **Maximum pending orders.** The front end hard-codes
  `turbineMaxPendingOrders = 5`. The server likely does not enforce
  this, but exceeding it is a good way to get yourself flagged.
* **Minimum order size.** Server rejects anything worth less than
  **$30 USD** with `HTTP 400 INPUT_VALIDATION_ERROR` — see
  [orders.md](orders.md).
* **No public changelog.** The backend has changed enum spellings and
  serialization details between deployments (a full redeploy bumped the
  API to `version: "0.114.1"` and rotated the settler/LP contract
  addresses). Pin the `/api/config` fields and validate responses
  strictly.
