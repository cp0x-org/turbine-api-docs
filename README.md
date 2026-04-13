# turbine-api-docs

Community-maintained reference for the **Turbine Alpha** HTTP API by
[PropellerHeads](https://propellerheads.xyz).

> **Disclaimer.** This is **not** official documentation. It is a
> reverse-engineered reference reconstructed from the public
> `app.turbine.exchange` JavaScript source map (shipped with source
> maps enabled as of 2026-04-13) and verified against the live
> production API. Accuracy reflects observations on that date; Turbine
> is in Alpha and the API may change without notice. For official
> information see [docs.propellerheads.xyz](https://docs.propellerheads.xyz)
> (Turbine Docs listed as "soon" at the time of writing).

## Why this exists

PropellerHeads ship Turbine as a private-beta Alpha with the front-end
at `app.turbine.exchange` and no public SDK documentation. If you are
a developer, a market maker, an AI agent, or an integrator trying to
understand the API shape without re-deriving it yourself, this repo
saves you a few hours.

## What's in here

- **[docs/overview.md](docs/overview.md)** — what Turbine is, who the
  executor is, where settlement happens
- **[docs/authentication.md](docs/authentication.md)** — SIWE flow
  (`/nonce` → `personal_sign` → `/verify`), cookie session
- **[docs/api-reference.md](docs/api-reference.md)** — every endpoint
  with payloads: `/config`, `/status`, `/nonce`, `/verify`, `/me`,
  `/logout`, `/add_order`, `/add_orders`, `/cancel_order`,
  `/order_states`, `/order_fees`, `/add_liquidity`, `/remove_liquidity`,
  `/liquidity_intent_states`
- **[docs/orders.md](docs/orders.md)** — `OrderIntent` field semantics,
  `midPriceDelta` bps meaning, partial fills, expiry, salt, minimum
  order size
- **[docs/permit2.md](docs/permit2.md)** — on-chain ERC-20 approval,
  `PermitSingle` EIP-712 typed-data signing, nonce semantics, settler
  as spender
- **[docs/wire-format.md](docs/wire-format.md)** — the non-obvious bits:
  hex-prefixed bigint serialization, the `yParity` bool-vs-string split
  between `/verify` and `/add_order`, `status` enum case, bigint
  representation choices
- **[docs/fees.md](docs/fees.md)** — **the platform fee, how it is
  computed, and how it interacts with spread**
- **[examples/minimal_client.py](examples/minimal_client.py)** — a
  self-contained Python reference client

## Quick reference (one-pager)

- **Base URL:** `https://api.turbine.exchange/api`
- **Chain:** Ethereum mainnet (chainId 1)
- **Auth:** SIWE `personal_sign`, session in HTTP cookie
- **Orders:** signed only via Permit2 `PermitSingle` (the `OrderIntent`
  itself is **not** EIP-712 signed)
- **Minimum order size:** **$30 USD** (enforced server-side with
  HTTP 400 `INPUT_VALIDATION_ERROR`)
- **Platform fee:** ~**0.99 bps** (0.0099%) of the mid-price notional,
  flat across size / side / delta — see [docs/fees.md](docs/fees.md)
- **BigInt wire format:** hex strings with `0x` prefix in responses;
  input accepts both decimal and hex
- **Polling:** `/order_states` is the primary fill source; the
  official JS SDK polls every 6 s with a client-side concurrency lock
- **Status enum seen in the wild:** `"Active"` (TitleCase) for resting
  orders; other values inferred but not yet verified

## Known contract addresses (Ethereum mainnet, 2026-04-13)

| Role | Address |
|------|---------|
| Turbine Settler | `0x49e9a8ea9b6c05d5b2307538d159350a5aea73ac` |
| Turbine Signer (gas-paying executor) | `0x89c740fea6bd1df86d0f8dff3f4c4c23cb598890` |
| LP Hook | `0x40bd6d8c59d43f6c345d79b17234d9b0e781a088` |
| LP Router | `0x4bd3f2ffc321f3ba4c3b31708212b76922f805a2` |
| Uniswap v4 Pool Manager | `0x000000000004444c5dc75cb358380d2e3de08a90` |
| Permit2 (standard Uniswap) | `0x000000000022D473030F116dDEE9F6B43aC78BA3` |

**Pin these.** The `/api/config` endpoint returns them — verify the
returned values against these pins on every startup. A mismatch
indicates an API / deployment change and your client should halt.

## Supported tokens

The SDK constants ship with: USDC, USDT, DAI, UNI, WETH, WEETH, PEPE,
WBTC. The `app.turbine.exchange` UI at the time of writing only
exposes USDC / WETH, but the SDK and server accept any registered
token pair.

## Accuracy and contributions

Everything here was verified by sending real (or quote-only) requests
against production Turbine on 2026-04-13. If you spot something that
has changed or is wrong, please open an issue or PR —
[CONTRIBUTING.md](CONTRIBUTING.md) has the process.

## License

[CC-BY-4.0](LICENSE). Free to share and adapt with attribution.
Backlink to this repository when copying substantial portions.

## Trademarks

"Turbine", "PropellerHeads", "Tycho", "Fynd" are marks of
PropellerHeads. This project is **not affiliated with, endorsed by, or
sponsored by PropellerHeads**. The name is used descriptively only.
