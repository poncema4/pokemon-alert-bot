"""Deterministic accuracy tests for retailer product detection and alert state."""
from datetime import datetime, timezone
from pathlib import Path
import sys

# GitHub Actions executes this file as ``python tests/test_accuracy.py``.
# In that mode Python puts ``tests/`` on sys.path, not the repository root,
# so explicitly add the project root before importing monitor.py.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitor import extract_structured_availability, is_pokemon, recently_stock_alerted, retailer_url_is_valid


def test_retailer_urls():
    assert retailer_url_is_valid("target", "https://www.target.com/p/-/A-123456")
    assert retailer_url_is_valid("walmart", "https://www.walmart.com/ip/123456")
    assert retailer_url_is_valid("walmart", "https://business.walmart.com/ip/pokemon-destined-rivals/19965460207")
    assert retailer_url_is_valid("bestbuy", "https://www.bestbuy.com/product/example/JJG123")
    assert retailer_url_is_valid("gamestop", "https://www.gamestop.com/toys-games/trading-cards/products/example/123.html")
    assert not retailer_url_is_valid("gamestop", "https://pokemondb.net")


def test_pokemon_detection():
    assert is_pokemon("Pokemon 30th Anniversary Elite Trainer Box")
    assert is_pokemon("Mega Evolution Pitch Black ETB")
    assert not is_pokemon("Nike hoodie")


def test_structured_stock_signals():
    assert extract_structured_availability('"availability":"https://schema.org/InStock"') is True
    assert extract_structured_availability('"availability":"https://schema.org/OutOfStock"') is False
    assert extract_structured_availability('<html>unknown</html>') is None


def test_unknown_does_not_block_restock_cooldown():
    now = datetime.now(timezone.utc)
    # Older state may contain the legacy last_alert field from UNKNOWN checks.
    # It must not suppress a real restock because only last_stock_alert is used.
    legacy_unknown = {"last_alert": now.isoformat()}
    assert recently_stock_alerted(legacy_unknown, now, 1) is False

    verified_recently = {"last_stock_alert": now.isoformat()}
    assert recently_stock_alerted(verified_recently, now, 1) is True


if __name__ == "__main__":
    test_retailer_urls()
    test_pokemon_detection()
    test_structured_stock_signals()
    test_unknown_does_not_block_restock_cooldown()
    print("accuracy smoke tests passed")
