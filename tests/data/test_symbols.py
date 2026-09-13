"""Venue-native symbol identity — Coinbase ≠ Binance markets."""

from __future__ import annotations

import pytest

from crypto_lab.data.symbols import (
    binance_symbol_for_asset,
    canonical_asset_for,
    coinbase_product_for_asset,
    normalize_symbol,
    parse_instrument,
    to_binance_symbol,
    to_coinbase_product,
)


def test_parse_binance():
    inst = parse_instrument("binance", "BTCUSDT")
    assert inst.source == "binance"
    assert inst.source_symbol == "BTCUSDT"
    assert inst.symbol == "BTCUSDT"
    assert inst.base_asset == "BTC"
    assert inst.quote_asset == "USDT"
    assert inst.canonical_asset == "BTC"


def test_parse_coinbase():
    inst = parse_instrument("coinbase", "BTC-USD")
    assert inst.source == "coinbase"
    assert inst.source_symbol == "BTC-USD"
    assert inst.symbol == "BTC-USD"
    assert inst.base_asset == "BTC"
    assert inst.quote_asset == "USD"
    assert inst.canonical_asset == "BTC"


def test_normalize_symbol_does_not_map_coinbase_to_usdt():
    assert normalize_symbol("BTC-USD", source="coinbase") == "BTC-USD"
    assert normalize_symbol("BTC-USD") == "BTC-USD"
    assert normalize_symbol("BTCUSDT", source="binance") == "BTCUSDT"
    with pytest.raises(ValueError):
        normalize_symbol("BTC-USD", source="binance")
    with pytest.raises(ValueError):
        normalize_symbol("BTCUSDT", source="coinbase")


def test_canonical_asset_is_asset_level_only():
    assert canonical_asset_for("binance", "BTCUSDT") == "BTC"
    assert canonical_asset_for("coinbase", "BTC-USD") == "BTC"
    assert canonical_asset_for("binance", "ETHUSDT") == "ETH"
    assert canonical_asset_for("coinbase", "ETH-USD") == "ETH"


def test_asset_helpers():
    assert binance_symbol_for_asset("BTC") == "BTCUSDT"
    assert coinbase_product_for_asset("BTC") == "BTC-USD"
    # Settings-style translation for API only — not identity equality
    assert to_coinbase_product("BTCUSDT") == "BTC-USD"
    assert to_binance_symbol("BTC-USD") == "BTCUSDT"
    assert to_binance_symbol("BTCUSDT") == "BTCUSDT"
    assert to_coinbase_product("ETH-USD") == "ETH-USD"


def test_no_cross_venue_market_equality():
    b = parse_instrument("binance", "BTCUSDT")
    c = parse_instrument("coinbase", "BTC-USD")
    assert b.source_symbol != c.source_symbol
    assert b.quote_asset != c.quote_asset
    assert b.canonical_asset == c.canonical_asset  # asset-level only
