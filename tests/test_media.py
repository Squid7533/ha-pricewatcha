"""Tests for the merchant page photo/condition extraction (3c)."""

from __future__ import annotations

import json

from custom_components.pricewatcha.media import parse_page_media


def _jsonld(data):
    return (
        '<script type="application/ld+json">'
        + json.dumps(data)
        + "</script>"
    )


def test_jsonld_product_image_and_condition():
    body = _jsonld(
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Apple iPhone Air 256GB",
            "image": ["https://cdn.example.com/a.jpg"],
            "offers": {
                "@type": "Offer",
                "itemCondition": "https://schema.org/RefurbishedCondition",
                "price": "669.00",
            },
        }
    )
    media = parse_page_media(body, "https://shop.example.com/p")
    assert media.image_url == "https://cdn.example.com/a.jpg"
    assert media.condition == "refurbished"
    assert media.title == "Apple iPhone Air 256GB"


def test_jsonld_graph_and_image_object():
    body = _jsonld(
        {
            "@graph": [
                {"@type": "WebPage", "name": "page"},
                {
                    "@type": "Product",
                    "name": "Phone",
                    "image": {"@type": "ImageObject", "url": "https://cdn.example.com/b.png"},
                    "offers": {"@type": "Offer", "itemCondition": "NewCondition"},
                },
            ]
        }
    )
    media = parse_page_media(body, "https://shop.example.com/p")
    assert media.image_url == "https://cdn.example.com/b.png"
    assert media.condition == "new"


def test_og_image_fallback_and_relative_url():
    body = (
        '<meta property="og:image" content="/media/a.jpg">'
        "<title>Phone</title>"
    )
    media = parse_page_media(body, "https://shop.example.com/product/1")
    assert media.image_url == "https://shop.example.com/media/a.jpg"
    assert media.condition is None


def test_no_image_returns_none_url():
    media = parse_page_media("<html><body>no product</body></html>", "https://shop.example.com")
    assert media.image_url is None
    assert media.condition is None


def test_broken_jsonld_does_not_hide_og_image():
    body = (
        '<script type="application/ld+json">{not json</script>'
        '<meta name="twitter:image" content="https://cdn.example.com/c.jpg">'
    )
    media = parse_page_media(body, "https://shop.example.com")
    assert media.image_url == "https://cdn.example.com/c.jpg"
