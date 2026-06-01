# turbine-api-docs

Community-maintained reference for the **Turbine Alpha** HTTP API by
[PropellerHeads](https://propellerheads.xyz).

> **Disclaimer.** This is **not** official documentation. It is a
> reverse-engineered reference reconstructed from the public
> `app.turbine.exchange` JavaScript source map (shipped with source
> maps enabled) and verified against the live production API. This
> revision tracks API **v0.114.1** (as reported by `/api/config`).
> Turbine is in Alpha and the API may change without notice. For
> official information see
> [docs.propellerheads.xyz](https://docs.propellerheads.xyz)
> (Turbine Docs listed as "soon" at the time of writing).

## For AI agents / LLMs

- **[llms.txt](llms.txt)** — this repo follows the
  [llmstxt.org](https://llmstxt.org) convention. Machine-readable
  index of every page with a one-sentence description. Point your
  tool at this file to discover the rest.
- **[llms-full.txt](llms-full.txt)** — all documentation
  concatenated into a single plain-text file (~1900 lines), suitable
  for loading into an LLM context window whole.
- **[examples/minimal_client.py](examples/minimal_client.py)** — a
  self-contained Python reference you can copy as a starting point.

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
  `spreadCurve` (delta curve over the order window; replaces the old
  scalar `midPriceDelta`), partial fills, expiry, salt, minimum
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
- **Platform fee:** query `/api/order_fees` (returns the fee in
  buy-token atomic units) — this is the source of truth. A previously
  observed "~0.99 bps flat" figure was an empirical reading on an older
  deployment and is **unverified** for v0.114 — see
  [docs/fees.md](docs/fees.md)
- **BigInt wire format:** hex strings with `0x` prefix in responses;
  input accepts both decimal and hex
- **Polling:** `/order_states` is the primary fill source; the
  official JS SDK polls every 6 s with a client-side concurrency lock
- **Order status enum (v0.114, TitleCase):** `Active`, `Filled`,
  `PendingCancellation`, `Canceled`, `Invalid`, `Expired`, `Adding`,
  `PartiallyFilled`, `Unknown` (`Adding` = order being added, pre-`Active`).
  `"Active"` is confirmed for resting orders; other values are taken from
  the SDK enum and not all directly observed

## Known contract addresses (Ethereum mainnet, v0.114.1)

| Role | Address |
|------|---------|
| Turbine Settler | `0xbb3e81c0563dc61719696475f5c7b5e011a73f8a` |
| Turbine Signer (gas-paying executor) | `0x89c740fea6bd1df86d0f8dff3f4c4c23cb598890` |
| LP Hook | `0xa44ff524f78858e015fcca322cb7d16aeb89a088` |
| LP Router | `0x8e7cc22eda4e2d3a8275fd88cf061681b42ce3d1` |
| Uniswap v4 Pool Manager | `0x000000000004444c5dc75cb358380d2e3de08a90` |
| Permit2 (standard Uniswap) | `0x000000000022D473030F116dDEE9F6B43aC78BA3` |

**Pin these.** The `/api/config` endpoint returns them — verify the
returned values against these pins on every startup. A mismatch
indicates an API / deployment change and your client should halt.

## Supported tokens

The full token list is returned by `/api/config` (v0.114 ships a much
larger set — on the order of several hundred tokens — each with CEX
oracle mappings: binance / bingx / bitget / coinbase / kraken / kucoin /
okx). Do not hardcode it; read it from `/api/config`. As examples,
USDC (`0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`) and WETH
(`0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2`) are present. The
`app.turbine.exchange` UI may expose only a subset (USDC / WETH), but
the server accepts any registered token pair.

## Accuracy and contributions

Most of this was verified by sending real (or quote-only) requests
against production Turbine; this revision reflects API v0.114.1. Some
response shapes are taken from the SDK source and are not all directly
verified — those are flagged where they appear. If you spot something
that has changed or is wrong, please open an issue or PR —
[CONTRIBUTING.md](CONTRIBUTING.md) has the process.

## License

[CC-BY-4.0](LICENSE). Free to share and adapt with attribution.
Backlink to this repository when copying substantial portions.

## Trademarks

"Turbine", "PropellerHeads", "Tycho", "Fynd" are marks of
PropellerHeads. This project is **not affiliated with, endorsed by, or
sponsored by PropellerHeads**. The name is used descriptively only.
