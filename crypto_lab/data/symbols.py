"""Venue-native instrument identity (no cross-venue market equality).

Phase-2 instruments are identified by (source, source_symbol), not a shared
market id. Coinbase BTC-USD is NOT Binance BTCUSDT — USD ≠ USDT and the
venues are separate markets. `canonical_asset` is asset-level only (BTC/ETH)
for later comparison, never a same-price/same-market claim.

Settings may list Binance-style symbols (BTCUSDT, ETHUSDT). Providers translate
those to venue-native ids for API calls; storage always keeps venue-native
symbols.
"""

from __future__ import annotations

from dataclasses import dataclass

CANONICAL_ASSETS = frozenset({"BTC", "ETH"})

# Venue-native Phase-2 instruments: source_symbol -> (base, quote)
BINANCE_INSTRUMENTS: dict[str, tuple[str, str]] = {
    "BTCUSDT": ("BTC", "USDT"),
    "ETHUSDT": ("ETH", "USDT"),
}

COINBASE_INSTRUMENTS: dict[str, tuple[str, str]] = {
    "BTC-USD": ("BTC", "USD"),
    "ETH-USD": ("ETH", "USD"),
}

# All known venue-native market symbols (not interchangeable across venues)
KNOWN_MARKET_SYMBOLS = frozenset(BINANCE_INSTRUMENTS) | frozenset(COINBASE_INSTRUMENTS)

ASSET_TO_BINANCE = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
ASSET_TO_COINBASE = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


@dataclass(frozen=True)
class Instrument:
    """Parsed venue-native instrument."""

    source: str
    source_symbol: str
    base_asset: str
    quote_asset: str
    canonical_asset: str

    @property
    def symbol(self) -> str:
        """Venue-native market id (alias of source_symbol)."""
        return self.source_symbol


def _norm_source(source: str) -> str:
    return source.strip().lower()


def _norm_binance_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("-", "").replace("_", "")


def _norm_coinbase_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace("_", "-")
    return s


def parse_instrument(source: str, source_symbol: str) -> Instrument:
    """Parse a venue-native symbol for a known source into an Instrument.

    Does NOT map Coinbase products to Binance USDT symbols.
    """
    src = _norm_source(source)
    if src == "binance":
        sym = _norm_binance_symbol(source_symbol)
        pair = BINANCE_INSTRUMENTS.get(sym)
        if pair is None:
            raise ValueError(
                f"Unsupported Binance symbol: {source_symbol!r} "
                f"(known: {sorted(BINANCE_INSTRUMENTS)})"
            )
        base, quote = pair
        return Instrument(
            source="binance",
            source_symbol=sym,
            base_asset=base,
            quote_asset=quote,
            canonical_asset=base,
        )
    if src == "coinbase":
        sym = _norm_coinbase_symbol(source_symbol)
        pair = COINBASE_INSTRUMENTS.get(sym)
        if pair is None:
            raise ValueError(
                f"Unsupported Coinbase product: {source_symbol!r} "
                f"(known: {sorted(COINBASE_INSTRUMENTS)})"
            )
        base, quote = pair
        return Instrument(
            source="coinbase",
            source_symbol=sym,
            base_asset=base,
            quote_asset=quote,
            canonical_asset=base,
        )
    raise ValueError(f"Unknown source for instrument parse: {source!r}")


def normalize_symbol(symbol: str, *, source: str | None = None) -> str:
    """Return venue-native market symbol — never cross-maps Coinbase → USDT.

    When ``source`` is binance or coinbase, validates and returns the native id.
    Without source, accepts any known Phase-2 venue-native form as-is.
    """
    if source is not None and _norm_source(source) in {"binance", "coinbase"}:
        return parse_instrument(source, symbol).source_symbol

    s_cb = _norm_coinbase_symbol(symbol)
    if s_cb in COINBASE_INSTRUMENTS:
        return s_cb
    s_bn = _norm_binance_symbol(symbol)
    if s_bn in BINANCE_INSTRUMENTS:
        return s_bn
    raise ValueError(f"Invalid or unsupported symbol: {symbol!r} (source={source})")


def canonical_asset_for(source: str, source_symbol: str) -> str:
    """Asset-level id (BTC/ETH) only — not a cross-venue market id."""
    return parse_instrument(source, source_symbol).canonical_asset


def binance_symbol_for_asset(asset: str) -> str:
    a = asset.strip().upper()
    if a not in ASSET_TO_BINANCE:
        raise ValueError(f"Unsupported asset for Binance: {asset!r}")
    return ASSET_TO_BINANCE[a]


def coinbase_product_for_asset(asset: str) -> str:
    a = asset.strip().upper()
    if a not in ASSET_TO_COINBASE:
        raise ValueError(f"Unsupported asset for Coinbase: {asset!r}")
    return ASSET_TO_COINBASE[a]


def resolve_asset(symbol: str) -> str:
    """Extract base asset from settings-style or venue-native symbol / asset code.

    Used only to translate settings lists (e.g. BTCUSDT) into a venue API id.
    Does not assert cross-venue market equality.
    """
    s = symbol.strip().upper()
    if s in CANONICAL_ASSETS:
        return s
    s_cb = _norm_coinbase_symbol(s)
    if s_cb in COINBASE_INSTRUMENTS:
        return COINBASE_INSTRUMENTS[s_cb][0]
    s_bn = _norm_binance_symbol(s)
    if s_bn in BINANCE_INSTRUMENTS:
        return BINANCE_INSTRUMENTS[s_bn][0]
    raise ValueError(f"Cannot resolve asset from symbol: {symbol!r}")


def to_binance_symbol(symbol: str) -> str:
    """Map settings/list symbol to Binance native for API calls."""
    return binance_symbol_for_asset(resolve_asset(symbol))


def to_coinbase_product(symbol: str) -> str:
    """Map settings/list symbol to Coinbase product for API calls only.

    Example: settings ``BTCUSDT`` → API ``BTC-USD`` (storage remains BTC-USD).
    """
    return coinbase_product_for_asset(resolve_asset(symbol))


def is_known_market_symbol(symbol: str) -> bool:
    """True if symbol is a known Phase-2 venue-native market id (any venue)."""
    try:
        normalize_symbol(symbol)
        return True
    except ValueError:
        return False


def is_valid_instrument(source: str, symbol: str) -> bool:
    """Validate (source, symbol) as a known Phase-2 instrument pair."""
    src = _norm_source(source)
    if src in {"binance", "coinbase"}:
        try:
            parse_instrument(src, symbol)
            return True
        except ValueError:
            return False
    # Non-venue sources (tests / manual): accept any known venue-native form
    return is_known_market_symbol(symbol)


# Back-compat alias — previously meant "in {BTCUSDT, ETHUSDT}"; now venue-native
def is_valid_canonical(symbol: str, *, source: str | None = None) -> bool:
    if source is not None:
        return is_valid_instrument(source, symbol)
    return is_known_market_symbol(symbol)


# Deprecated name kept for imports that expected the old frozenset of market ids.
# Prefer KNOWN_MARKET_SYMBOLS / CANONICAL_ASSETS.
CANONICAL_SYMBOLS = KNOWN_MARKET_SYMBOLS
