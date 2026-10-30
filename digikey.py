"""Read-only DigiKey Product Information API v4 client.

Uses 2-legged OAuth (client credentials). Credentials come from DIGIKEY_CLIENT_ID /
DIGIKEY_CLIENT_SECRET, loaded from the same .env files as the InvenTree settings.
"""

import asyncio
import os
import re
import time
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse

import httpx

API_BASE = os.getenv("DIGIKEY_API_BASE", "https://api.digikey.com")

# DigiKey product URLs look like
#   https://www.digikey.com/en/products/detail/<manufacturer>/<mpn>/<digikey product id>
_DETAIL_URL = re.compile(r"/products/detail/[^/]+/([^/?#]+)")


def part_number_from(query: str) -> str:
    """Return a part number from a DigiKey product URL, or the query itself."""
    query = query.strip()
    if "://" in query or query.startswith("www."):
        path = urlparse(query if "://" in query else f"https://{query}").path
        m = _DETAIL_URL.search(path)
        if not m:
            raise ValueError(f"Not a DigiKey product URL: {query}")
        return unquote(m.group(1))
    return query


class DigiKeyClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.site = os.getenv("DIGIKEY_SITE", "US")
        self.language = os.getenv("DIGIKEY_LANGUAGE", "en")
        self.currency = os.getenv("DIGIKEY_CURRENCY", "USD")
        self._token: Optional[str] = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(base_url=API_BASE, timeout=30)

    async def _get_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._token_expiry - 30:
                return self._token
            r = await self._http.post(
                "/v1/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
            )
            if r.status_code != 200:
                raise RuntimeError(f"DigiKey auth failed ({r.status_code}): {r.text[:300]}")
            body = r.json()
            self._token = body["access_token"]
            self._token_expiry = time.monotonic() + int(body.get("expires_in", 600))
            return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {
            "Authorization": f"Bearer {await self._get_token()}",
            "X-DIGIKEY-Client-Id": self.client_id,
            "X-DIGIKEY-Locale-Site": self.site,
            "X-DIGIKEY-Locale-Language": self.language,
            "X-DIGIKEY-Locale-Currency": self.currency,
        }
        r = await self._http.request(method, path, headers=headers, **kwargs)
        if r.status_code == 404:
            raise LookupError("DigiKey has no product matching that part number (404)")
        if r.status_code >= 300:
            raise RuntimeError(f"DigiKey API error {r.status_code}: {r.text[:300]}")
        return r.json()

    async def lookup(self, query: str) -> dict:
        """Full product details for a DigiKey URL, DigiKey part number, or manufacturer part number."""
        pn = part_number_from(query)
        data = await self._request("GET", f"/products/v4/search/{quote(pn, safe='')}/productdetails")
        return {"query": query, "part_number_searched": pn, "currency": self.currency,
                **_product(data.get("Product") or {})}

    async def search(self, keywords: str, limit: int = 10) -> dict:
        """Keyword search; returns a short summary per product."""
        data = await self._request(
            "POST", "/products/v4/search/keyword",
            json={"Keywords": keywords, "Limit": max(1, min(limit, 50)), "Offset": 0},
        )
        return {
            "keywords": keywords,
            "total": data.get("ProductsCount"),
            "currency": self.currency,
            "exact_matches": [_summary(p) for p in data.get("ExactMatches") or []],
            "results": [_summary(p) for p in data.get("Products") or []],
        }


def _name(obj: Any, key: str = "Name") -> Optional[str]:
    return obj.get(key) if isinstance(obj, dict) else None


def _description(p: dict) -> tuple[Optional[str], Optional[str]]:
    d = p.get("Description") or {}
    return d.get("ProductDescription"), d.get("DetailedDescription")


def _status(p: dict) -> Optional[str]:
    st = p.get("ProductStatus")
    return (st.get("Status") or st.get("Text")) if isinstance(st, dict) else st


def _category_path(p: dict) -> list[str]:
    """Walk Category -> ChildCategories (first child each level) into a name path."""
    path, cat = [], p.get("Category")
    while isinstance(cat, dict) and cat.get("Name"):
        path.append(cat["Name"])
        children = cat.get("ChildCategories") or []
        cat = children[0] if children else None
    return path


def _variations(p: dict) -> list[dict]:
    out = []
    for v in p.get("ProductVariations") or []:
        out.append({
            "digikey_part_number": v.get("DigiKeyProductNumber"),
            "package": _name(v.get("PackageType")),
            "minimum_order_quantity": v.get("MinimumOrderQuantity"),
            "standard_package": v.get("StandardPackage"),
            "quantity_available": v.get("QuantityAvailableforPackageType"),
            "price_breaks": [
                {"quantity": b.get("BreakQuantity"), "unit_price": b.get("UnitPrice")}
                for b in v.get("StandardPricing") or []
            ],
        })
    return out


def _product(p: dict) -> dict:
    short, detailed = _description(p)
    return {
        "manufacturer": _name(p.get("Manufacturer")),
        "manufacturer_part_number": p.get("ManufacturerProductNumber"),
        "description": short,
        "detailed_description": detailed,
        "category_path": _category_path(p),
        "status": _status(p),
        "quantity_available": p.get("QuantityAvailable"),
        "unit_price": p.get("UnitPrice"),
        "product_url": p.get("ProductUrl"),
        "datasheet_url": p.get("DatasheetUrl"),
        "photo_url": p.get("PhotoUrl"),
        "parameters": {
            prm.get("ParameterText"): prm.get("ValueText")
            for prm in p.get("Parameters") or []
            if prm.get("ParameterText")
        },
        "variations": _variations(p),
    }


def _summary(p: dict) -> dict:
    short, _ = _description(p)
    return {
        "manufacturer": _name(p.get("Manufacturer")),
        "manufacturer_part_number": p.get("ManufacturerProductNumber"),
        "description": short,
        "status": _status(p),
        "quantity_available": p.get("QuantityAvailable"),
        "unit_price": p.get("UnitPrice"),
        "product_url": p.get("ProductUrl"),
        "digikey_part_numbers": [
            v.get("DigiKeyProductNumber") for v in p.get("ProductVariations") or []
        ],
    }
