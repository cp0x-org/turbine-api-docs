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

The user — or your bot — controls three things per order:

1. **Token pair, amount, and direction** — what you want to trade.
2. **`midPriceDelta` in basis points** — your price tolerance relative
   to the current mid-price. Negative means "I want better than mid"
   (market maker / earn side). Positive means "I tolerate worse than
   mid" (taker / fast). Range is `-10000` to `+10000` (±100%).
3. **Expiry** — `start_time` / `end_time` Unix seconds. Orders
   typically last up to an hour.

Everything else (matching, order book position, execution timing,
gas optimisation) is the solver's problem.

## The three-contract on-chain surface

Despite being an off-chain solver, Turbine has a small on-chain
footprint you will interact with when setting up or monitoring
positions:

| Contract | Role |
|---|---|
| **Permit2** (`0x0000…3ac78BA3`) | Standard Uniswap Permit2. You approve this at the ERC-20 level, then sign per-order `PermitSingle` messages off-chain. Executor calls `transferFrom` through Permit2 at settlement time. |
| **Turbine Settler** (`0x49e9…73aC`) | The contract that receives the final `settle(...)` call from the executor. This is the `spender` in your signed `PermitSingle`. |
| **Turbine Signer / Executor** (`0x89c7…8890`) | The EOA that pays gas and submits settlement transactions. Monitor its balance if you are market-making — if it goes dark, your orders stop settling. |
| **LP Hook / Router / Pool Manager** | Only relevant if you interact with Turbine's Uniswap-v4-style LP surface. Not needed for plain intents. |

See [permit2.md](permit2.md) for the exact ERC-20 approval and signing
flow, and [api-reference.md](api-reference.md) for the
`GET /api/config` endpoint that returns these addresses (and which you
should pin).

## The executor (operational reality)

The executor wallet is a **single EOA** that signs and submits every
Turbine settlement. At the time of this writing (2026-04-13) we
observe:

* Nonce ~137 over the wallet's entire lifetime — small total Alpha
  volume.
* Balance around ~0.0045 ETH at most observations, indicating
  **just-in-time funding** (PropellerHeads tops up shortly before
  they need to settle).
* Settlement transactions batch multiple orders into one tx where
  possible; the actual on-chain method selector is `0xfdcc8090`
  (`settle` on the Turbine Settler contract).

If you run an automated client, treat executor balance as a soft
signal — warn when it drops below ~0.01 ETH, but do not halt on it
(you do not control it). The [bot we built for our own MM
experiment](https://github.com/illlefr4u/turbine-apy) uses a 5-minute
periodic executor-balance poll and surfaces warnings only.

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
* **No public changelog.** The backend can (and did during our
  integration) change enum spellings and serialization details. Pin
  the `/api/config` fields and validate responses strictly.
