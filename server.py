#!/usr/bin/env python3
"""InvenTree MCP Server — 13 parameterized tools, 130 operations.

Dual transport: STDIO (default) or SSE (pass 'sse' argument).
Uses inventree-python library wrapped in asyncio.to_thread() for async safety.
"""

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Real env vars win, then a .env next to server.py (repo clone), then the per-machine
# config file (used when installed via uvx, where there is no repo directory).
load_dotenv()
load_dotenv(Path(os.getenv("INVENTREE_MCP_ENV", "~/.config/inventree-mcp/.env")).expanduser())

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

mcp = FastMCP("inventree_mcp")

# ── Lazy client singleton ────────────────────────────────────────────

_client = None
_client_lock = asyncio.Lock()


async def get_client():
    """Get or create the InvenTree client singleton."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        from client import InvenTreeClient

        url = os.getenv("INVENTREE_URL")
        token = os.getenv("INVENTREE_TOKEN")
        if not url or not token:
            raise RuntimeError(
                "INVENTREE_URL and INVENTREE_TOKEN must be set (env vars, .env, or ~/.config/inventree-mcp/.env)"
            )
        client = InvenTreeClient(url, token)
        await client.connect()
        _client = client
        logger.info(f"InvenTree client connected to {url}")
    return _client


def _json(data: Any) -> str:
    """Serialize response data to JSON string."""
    return json.dumps(data, indent=2, default=str)


def _error(e: Exception, context: str = "") -> str:
    """Format an actionable error message for the agent."""
    # inventree-python embeds request headers (incl. the auth token) in exception text
    msg = re.sub(r"((?:Token|Bearer)\s+)[^'\"\s,}]+", r"\1[REDACTED]", str(e))
    if "DIGIKEY_CLIENT" in msg or "DigiKey auth" in msg:
        hint = "Set DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET (developer.digikey.com app, Product Information v4)."
    elif "DigiKey" in msg:
        hint = "Check the DigiKey URL or part number; try digikey.search with keywords."
    elif "404" in msg or "not found" in msg.lower():
        hint = "Check that the ID exists. Use the list/search operation first to find valid IDs."
    elif "403" in msg or "permission" in msg.lower() or "forbidden" in msg.lower():
        hint = "The API token lacks permission for this operation. Check INVENTREE_TOKEN roles."
    elif "400" in msg or "bad request" in msg.lower():
        hint = "Invalid request data. Check required fields in the 'data' parameter."
    elif "timeout" in msg.lower() or "connect" in msg.lower():
        hint = "Connection failed. Verify INVENTREE_URL is reachable."
    elif "INVENTREE_URL" in msg or "INVENTREE_TOKEN" in msg:
        hint = "Set INVENTREE_URL and INVENTREE_TOKEN environment variables."
    else:
        hint = "Check the operation name and parameters."
    prefix = f"[{context}] " if context else ""
    logger.error(f"{prefix}{msg}")
    return _json({"error": f"{prefix}{msg}", "hint": hint})


def _safe(tool_name: str):
    """Decorator to add error handling to tool functions."""
    import functools

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                op = kwargs.get("operation", args[0] if args else "unknown")
                return _error(e, f"{tool_name}.{op}")
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════════
# Tool Definitions — 13 parameterized tools
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool(
    annotations={
        "title": "InvenTree Part & Category Management",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("part")
async def part(
    operation: str,
    pk: int = None,
    data: dict = None,
    search: str = None,
    category: int = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Part & category management in InvenTree.

    Operations:
      Read: list, get, get_stock, get_bom, get_bom_usage, get_suppliers,
        get_manufacturers, get_parameters, get_parameter_templates,
        get_test_templates, get_related, get_builds, get_internal_prices,
        get_sale_prices, list_categories, get_category, get_category_parameters
      Write: create, update, create_category, update_category,
        create_parameter_template, update_parameter_template,
        add_category_parameter, set_parameter
      Delete: delete

    Parameters (InvenTree 1.x): create templates first, attach them to categories so new
    parts get them by default, then set values on parts. Creating/updating templates requires
    a staff user (InvenTree ignores group roles there); a 403 means ask the user to create the
    template in the InvenTree admin or temporarily make the MCP user staff.
      create_parameter_template: data={"name": "Resistance", "units": "ohm", "description": "..."}
        (optional: "choices": "0402,0603,0805", "checkbox": true, "model_type": "part")
      update_parameter_template: pk=template ID, data=fields to change
      add_category_parameter: pk=category ID, data={"template": <template ID>, "default_value": "..."}
      set_parameter: pk=part ID, data={"template": <template ID>, "data": "10k", "note": "..."}
        (updates the value if the part already has that parameter)

    Args:
        operation: One of the operations listed above.
        pk: Part, category, or parameter template ID (see operations above).
        data: Dict of fields for create/update (e.g. {"name": "Seal Kit", "category": 5}).
        search: Text search filter for list/list_categories.
        category: Category ID filter for list.
        limit: Max results for list (default 25).
        offset: Pagination offset for list.

    Returns:
        JSON string with part/category data or {"error": "..."}.
    """
    c = await get_client()

    if operation == "list":
        filters = {}
        if search:
            filters["search"] = search
        if category is not None:
            filters["category"] = category
        filters["limit"] = limit
        filters["offset"] = offset
        return _json(await c.part_list(**filters))

    elif operation == "get":
        return _json(await c.part_get(pk))

    elif operation == "create":
        return _json(await c.part_create(data or {}))

    elif operation == "update":
        return _json(await c.part_update(pk, data or {}))

    elif operation == "delete":
        return _json(await c.part_delete(pk))

    elif operation == "get_stock":
        return _json(await c.part_get_stock(pk))

    elif operation == "get_bom":
        return _json(await c.part_get_bom(pk))

    elif operation == "get_bom_usage":
        return _json(await c.part_get_bom_usage(pk))

    elif operation == "get_suppliers":
        return _json(await c.part_get_suppliers(pk))

    elif operation == "get_manufacturers":
        return _json(await c.part_get_manufacturers(pk))

    elif operation == "get_parameters":
        return _json(await c.part_get_parameters(pk))

    elif operation == "get_parameter_templates":
        return _json(await c.part_get_parameter_templates())

    elif operation == "create_parameter_template":
        return _json(await c.part_create_parameter_template(data or {}))

    elif operation == "update_parameter_template":
        return _json(await c.part_update_parameter_template(pk, data or {}))

    elif operation == "set_parameter":
        return _json(await c.part_set_parameter(pk, data or {}))

    elif operation == "get_test_templates":
        return _json(await c.part_get_test_templates(pk))

    elif operation == "get_related":
        return _json(await c.part_get_related(pk))

    elif operation == "get_builds":
        return _json(await c.part_get_builds(pk))

    elif operation == "get_internal_prices":
        return _json(await c.part_get_internal_prices(pk))

    elif operation == "get_sale_prices":
        return _json(await c.part_get_sale_prices(pk))

    elif operation == "list_categories":
        filters = {}
        if search:
            filters["search"] = search
        return _json(await c.part_list_categories(**filters))

    elif operation == "get_category":
        return _json(await c.part_get_category(pk))

    elif operation == "create_category":
        return _json(await c.part_create_category(data or {}))

    elif operation == "get_category_parameters":
        return _json(await c.part_get_category_parameters(pk))

    elif operation == "update_category":
        return _json(await c.part_update_category(pk, data or {}))

    elif operation == "add_category_parameter":
        return _json(await c.part_add_category_parameter(pk, data or {}))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Stock & Location Management",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("stock")
async def stock(
    operation: str,
    pk: int = None,
    data: dict = None,
    part_id: int = None,
    location_id: int = None,
    quantity: float = None,
    test_name: str = None,
    test_result: bool = None,
    search: str = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Stock item & location management in InvenTree.

    Operations:
      Read: list, get, get_by_location, get_by_part, list_locations,
        get_location, get_tracking, get_test_results
      Write: create, update, transfer, count, add, remove, create_location,
        update_location, upload_test_result

    update edits a stock item; update_location edits a location (pk=location ID), e.g.
    data={"structural": true}, {"name": "...", "description": "..."}, {"parent": <location ID>}.

    Args:
        operation: One of the operations listed above.
        pk: Stock item or location ID.
        data: Dict of fields for create/update.
        part_id: Filter stock by part (for list, get_by_part).
        location_id: Filter stock by location or transfer destination.
        quantity: Quantity for transfer/count/add/remove operations.
        test_name: Name for upload_test_result.
        test_result: Boolean pass/fail for upload_test_result.
        search: Text search filter for list/list_locations.
        limit: Max results (default 25).
        offset: Pagination offset.

    Returns:
        JSON string with stock/location data or {"error": "..."}.
    """
    c = await get_client()

    if operation == "list":
        filters = {"limit": limit, "offset": offset}
        if search:
            filters["search"] = search
        if part_id is not None:
            filters["part"] = part_id
        if location_id is not None:
            filters["location"] = location_id
        return _json(await c.stock_list(**filters))

    elif operation == "get":
        return _json(await c.stock_get(pk))

    elif operation == "create":
        return _json(await c.stock_create(data or {}))

    elif operation == "update":
        return _json(await c.stock_update(pk, data or {}))

    elif operation == "transfer":
        return _json(await c.stock_transfer(pk, location_id, quantity))

    elif operation == "count":
        return _json(await c.stock_count(pk, quantity))

    elif operation == "add":
        return _json(await c.stock_add(pk, quantity))

    elif operation == "remove":
        return _json(await c.stock_remove(pk, quantity))

    elif operation == "get_by_location":
        return _json(await c.stock_get_by_location(location_id))

    elif operation == "get_by_part":
        return _json(await c.stock_get_by_part(part_id))

    elif operation == "list_locations":
        filters = {}
        if search:
            filters["search"] = search
        return _json(await c.stock_list_locations(**filters))

    elif operation == "get_location":
        return _json(await c.stock_get_location(pk))

    elif operation == "create_location":
        return _json(await c.stock_create_location(data or {}))

    elif operation == "update_location":
        return _json(await c.stock_update_location(pk, data or {}))

    elif operation == "get_tracking":
        return _json(await c.stock_get_tracking(pk))

    elif operation == "upload_test_result":
        extra = {}
        if "notes" in (data or {}):
            extra["notes"] = data["notes"]
        if "value" in (data or {}):
            extra["value"] = data["value"]
        return _json(await c.stock_upload_test_result(pk, test_name, test_result, **extra))

    elif operation == "get_test_results":
        return _json(await c.stock_get_test_results(pk))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Build Order Management",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("build_order")
async def build_order(
    operation: str,
    pk: int = None,
    data: dict = None,
    items: list = None,
    search: str = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Manufacturing build order management in InvenTree.

    Operations:
      Read: list, get, get_outputs, get_lines
      Write: create, update, allocate, complete, cancel

    Args:
        operation: One of the operations listed above.
        pk: Build order ID.
        data: Dict of fields for create/update/complete.
        items: List of allocation dicts for allocate (e.g. [{"stock_item": 1, "quantity": 5, "build_line": 3}]).
        search: Text search filter for list.
        limit: Max results (default 25).
        offset: Pagination offset.

    Returns:
        JSON string with build order data or {"error": "..."}.
    """
    c = await get_client()

    if operation == "list":
        filters = {"limit": limit, "offset": offset}
        if search:
            filters["search"] = search
        return _json(await c.build_list(**filters))

    elif operation == "get":
        return _json(await c.build_get(pk))

    elif operation == "create":
        return _json(await c.build_create(data or {}))

    elif operation == "update":
        return _json(await c.build_update(pk, data or {}))

    elif operation == "allocate":
        return _json(await c.build_allocate(pk, items or []))

    elif operation == "complete":
        return _json(await c.build_complete(pk, **(data or {})))

    elif operation == "cancel":
        return _json(await c.build_cancel(pk))

    elif operation == "get_outputs":
        return _json(await c.build_get_outputs(pk))

    elif operation == "get_lines":
        return _json(await c.build_get_lines(pk))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Purchase Order Management",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("purchase_order")
async def purchase_order(
    operation: str,
    pk: int = None,
    data: dict = None,
    location_id: int = None,
    items: list = None,
    search: str = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Purchase order lifecycle management in InvenTree.

    Operations:
      Read: list, get, get_line_items
      Write: create, update, issue, receive, complete, cancel, hold,
        add_line_item, add_extra_line_item

    Args:
        operation: One of the operations listed above.
        pk: Purchase order ID.
        data: Dict of fields for create/update/add_line_item.
        location_id: Receiving location for receive operation.
        items: Line items to receive (e.g. [{"line_item": 1, "quantity": 10}]).
        search: Text search filter for list.
        limit: Max results (default 25).
        offset: Pagination offset.

    Returns:
        JSON string with PO data or {"error": "..."}.
    """
    c = await get_client()

    if operation == "list":
        filters = {"limit": limit, "offset": offset}
        if search:
            filters["search"] = search
        return _json(await c.po_list(**filters))

    elif operation == "get":
        return _json(await c.po_get(pk))

    elif operation == "create":
        return _json(await c.po_create(data or {}))

    elif operation == "update":
        return _json(await c.po_update(pk, data or {}))

    elif operation == "issue":
        return _json(await c.po_issue(pk))

    elif operation == "receive":
        return _json(await c.po_receive(pk, location_id, items))

    elif operation == "complete":
        return _json(await c.po_complete(pk))

    elif operation == "cancel":
        return _json(await c.po_cancel(pk))

    elif operation == "hold":
        return _json(await c.po_hold(pk))

    elif operation == "add_line_item":
        return _json(await c.po_add_line_item(pk, data or {}))

    elif operation == "add_extra_line_item":
        return _json(await c.po_add_extra_line_item(pk, data or {}))

    elif operation == "get_line_items":
        return _json(await c.po_get_line_items(pk))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Sales Order Management",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("sales_order")
async def sales_order(
    operation: str,
    pk: int = None,
    data: dict = None,
    shipment_id: int = None,
    reference: str = None,
    search: str = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Sales order lifecycle management in InvenTree.

    Operations:
      Read: list, get, get_line_items, get_allocations
      Write: create, update, complete, cancel, hold, add_line_item,
        add_extra_line_item, create_shipment, complete_shipment

    Args:
        operation: One of the operations listed above.
        pk: Sales order ID.
        data: Dict of fields for create/update/add_line_item.
        shipment_id: Shipment ID for complete_shipment.
        reference: Reference string for create_shipment.
        search: Text search filter for list.
        limit: Max results (default 25).
        offset: Pagination offset.

    Returns:
        JSON string with SO data or {"error": "..."}.
    """
    c = await get_client()

    if operation == "list":
        filters = {"limit": limit, "offset": offset}
        if search:
            filters["search"] = search
        return _json(await c.so_list(**filters))

    elif operation == "get":
        return _json(await c.so_get(pk))

    elif operation == "create":
        return _json(await c.so_create(data or {}))

    elif operation == "update":
        return _json(await c.so_update(pk, data or {}))

    elif operation == "complete":
        return _json(await c.so_complete(pk))

    elif operation == "cancel":
        return _json(await c.so_cancel(pk))

    elif operation == "hold":
        return _json(await c.so_hold(pk))

    elif operation == "add_line_item":
        return _json(await c.so_add_line_item(pk, data or {}))

    elif operation == "add_extra_line_item":
        return _json(await c.so_add_extra_line_item(pk, data or {}))

    elif operation == "get_line_items":
        return _json(await c.so_get_line_items(pk))

    elif operation == "create_shipment":
        return _json(await c.so_create_shipment(pk, reference, **(data or {})))

    elif operation == "complete_shipment":
        return _json(await c.so_complete_shipment(shipment_id, **(data or {})))

    elif operation == "get_allocations":
        return _json(await c.so_get_allocations(pk))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Return Order Management",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("return_order")
async def return_order(
    operation: str,
    pk: int = None,
    data: dict = None,
    search: str = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Return order lifecycle management in InvenTree.

    Operations:
      Read: list, get, get_line_items
      Write: create, update, complete, cancel, add_line_item

    Args:
        operation: One of the operations listed above.
        pk: Return order ID.
        data: Dict of fields for create/update/add_line_item.
        search: Text search filter for list.
        limit: Max results (default 25).
        offset: Pagination offset.

    Returns:
        JSON string with RO data or {"error": "..."}.
    """
    c = await get_client()

    if operation == "list":
        filters = {"limit": limit, "offset": offset}
        if search:
            filters["search"] = search
        return _json(await c.ro_list(**filters))

    elif operation == "get":
        return _json(await c.ro_get(pk))

    elif operation == "create":
        return _json(await c.ro_create(data or {}))

    elif operation == "update":
        return _json(await c.ro_update(pk, data or {}))

    elif operation == "complete":
        return _json(await c.ro_complete(pk))

    elif operation == "cancel":
        return _json(await c.ro_cancel(pk))

    elif operation == "add_line_item":
        return _json(await c.ro_add_line_item(pk, data or {}))

    elif operation == "get_line_items":
        return _json(await c.ro_get_line_items(pk))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Company Management",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("company")
async def company(
    operation: str,
    pk: int = None,
    data: dict = None,
    company_id: int = None,
    supplier_part_id: int = None,
    part_id: int = None,
    search: str = None,
    is_supplier: bool = None,
    is_manufacturer: bool = None,
    is_customer: bool = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Supplier, manufacturer, and customer management in InvenTree.

    Operations:
      Read: list, get, list_supplier_parts, get_supplier_part,
        list_manufacturer_parts, get_manufacturer_part, list_contacts,
        list_addresses, get_price_breaks
      Write: create, update, create_supplier_part, update_supplier_part,
        create_manufacturer_part, update_manufacturer_part, add_price_break
      Delete: delete

    Linking a part to where it's bought (check list_supplier_parts with part_id first to avoid
    duplicates):
      1. The supplier/manufacturer must exist as a company: create with
         data={"name": "Amazon", "is_supplier": true, "website": "https://www.amazon.com"}.
      2. Optional, when the maker's part number matters: create_manufacturer_part with
         data={"part": <part ID>, "manufacturer": <company ID>, "MPN": "RC0603FR-0710KL", "link": "..."}.
      3. create_supplier_part with data={"part": <part ID>, "supplier": <company ID>,
         "SKU": "<supplier's order code, e.g. Amazon ASIN>", "link": "<product URL>",
         "manufacturer_part": <manufacturer part ID, optional>, "pack_quantity": "100", "note": "..."}.
      4. Optional price: add_price_break with supplier_part_id and
         data={"quantity": 1, "price": 5.99, "price_currency": "USD"}.

    Args:
        operation: One of the operations listed above.
        pk: Company, supplier part, or manufacturer part ID (for get/update operations).
        data: Dict of fields for create/update.
        company_id: Company ID for list_supplier_parts/list_manufacturer_parts/list_contacts/list_addresses.
        supplier_part_id: Supplier part ID for get_price_breaks/add_price_break.
        part_id: Part ID filter for list_supplier_parts/list_manufacturer_parts.
        search: Text search filter for list/list_supplier_parts/list_manufacturer_parts.
        is_supplier/is_manufacturer/is_customer: Boolean filters for list.
        limit: Max results (default 25).
        offset: Pagination offset.

    Returns:
        JSON string with company data or {"error": "..."}.
    """
    c = await get_client()

    if operation == "list":
        filters = {"limit": limit, "offset": offset}
        if search:
            filters["search"] = search
        if is_supplier is not None:
            filters["is_supplier"] = is_supplier
        if is_manufacturer is not None:
            filters["is_manufacturer"] = is_manufacturer
        if is_customer is not None:
            filters["is_customer"] = is_customer
        return _json(await c.company_list(**filters))

    elif operation == "get":
        return _json(await c.company_get(pk))

    elif operation == "create":
        return _json(await c.company_create(data or {}))

    elif operation == "update":
        return _json(await c.company_update(pk, data or {}))

    elif operation == "delete":
        return _json(await c.company_delete(pk))

    elif operation == "list_supplier_parts":
        filters = {}
        if company_id is not None:
            filters["supplier"] = company_id
        if part_id is not None:
            filters["part"] = part_id
        if search:
            filters["search"] = search
        return _json(await c.company_list_supplier_parts(**filters))

    elif operation == "get_supplier_part":
        return _json(await c.company_get_supplier_part(pk))

    elif operation == "list_manufacturer_parts":
        filters = {}
        if company_id is not None:
            filters["manufacturer"] = company_id
        if part_id is not None:
            filters["part"] = part_id
        if search:
            filters["search"] = search
        return _json(await c.company_list_manufacturer_parts(**filters))

    elif operation == "get_manufacturer_part":
        return _json(await c.company_get_manufacturer_part(pk))

    elif operation == "list_contacts":
        return _json(await c.company_list_contacts(company_id or pk))

    elif operation == "list_addresses":
        return _json(await c.company_list_addresses(company_id or pk))

    elif operation == "get_price_breaks":
        return _json(await c.company_get_price_breaks(supplier_part_id or pk))

    elif operation == "create_supplier_part":
        return _json(await c.company_create_supplier_part(data or {}))

    elif operation == "update_supplier_part":
        return _json(await c.company_update_supplier_part(pk, data or {}))

    elif operation == "create_manufacturer_part":
        return _json(await c.company_create_manufacturer_part(data or {}))

    elif operation == "update_manufacturer_part":
        return _json(await c.company_update_manufacturer_part(pk, data or {}))

    elif operation == "add_price_break":
        return _json(await c.company_add_price_break(supplier_part_id or pk, data or {}))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Barcode Operations",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("barcode")
async def barcode(
    operation: str,
    barcode_data: str = None,
    model_type: str = None,
    pk: int = None,
) -> str:
    """Barcode scanning and assignment in InvenTree.

    Operations:
      Read: scan, lookup
      Write: assign, unassign

    Args:
        operation: One of the operations listed above.
        barcode_data: Barcode string for scan/assign/lookup.
        model_type: Model type for assign/unassign (part, stockitem, stocklocation, build, purchaseorder, salesorder, returnorder).
        pk: Object ID for assign/unassign.

    Returns:
        JSON string with barcode result or {"error": "..."}.
    """
    c = await get_client()

    if operation == "scan":
        return _json(await c.barcode_scan(barcode_data))

    elif operation == "assign":
        return _json(await c.barcode_assign(model_type, pk, barcode_data))

    elif operation == "unassign":
        return _json(await c.barcode_unassign(model_type, pk))

    elif operation == "lookup":
        return _json(await c.barcode_lookup(barcode_data))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Label Printing",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("label")
async def label(
    operation: str,
    label_type: str = "part",
    template_id: int = None,
    item_ids: list = None,
    plugin: str = None,
    destination: str = None,
) -> str:
    """Label printing and template management in InvenTree.

    Operations: list_templates, print_part, print_stock, print_location, download_template

    Args:
        operation: One of the operations listed above.
        label_type: Filter for list_templates (part, stock, location).
        template_id: Label template ID for print/download operations.
        item_ids: List of item IDs to print labels for.
        plugin: Optional printing plugin key (defaults to the server's default label printer).
        destination: Path relative to INVENTREE_MCP_FILES to save the template to (default: that directory).

    Returns:
        JSON string with label templates or print result.
    """
    c = await get_client()

    if operation == "list_templates":
        return _json(await c.label_list_templates(label_type))

    elif operation in ("print_part", "print_stock", "print_location"):
        return _json(await c.label_print(template_id, item_ids or [], plugin))

    elif operation == "download_template":
        return _json(await c.label_download_template(template_id, destination))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Report Generation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("report")
async def report(
    operation: str,
    template_id: int = None,
    item_ids: list = None,
    destination: str = None,
) -> str:
    """Report generation and template management in InvenTree.

    Operations: list_templates, print_report, download_template

    Args:
        operation: One of the operations listed above.
        template_id: Report template ID for print_report/download_template.
        item_ids: List of item IDs to include in report (must match the template's model type).
        destination: Path relative to INVENTREE_MCP_FILES to save the template to (default: that directory).

    Returns:
        JSON string with report templates or print result.
    """
    c = await get_client()

    if operation == "list_templates":
        return _json(await c.report_list_templates())

    elif operation == "print_report":
        return _json(await c.report_print(template_id, item_ids or []))

    elif operation == "download_template":
        return _json(await c.report_download_template(template_id, destination))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree Attachment Management",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@_safe("attachment")
async def attachment(
    operation: str,
    model_type: str = None,
    model_id: int = None,
    attachment_id: int = None,
    file_path: str = None,
    link: str = None,
    comment: str = "",
    destination: str = None,
) -> str:
    """File attachment management for any InvenTree object.

    Operations:
      Read: list, download
      Write: upload, upload_link
      Delete: delete

    Args:
        operation: One of the operations listed above.
        model_type: Object type (part, stockitem, build, purchaseorder, salesorder, company).
        model_id: Object ID for list/upload/upload_link.
        attachment_id: Attachment ID for download/delete.
        file_path: Path relative to INVENTREE_MCP_FILES of the file to upload.
        link: URL for upload_link.
        comment: Optional comment for uploads.
        destination: Path relative to INVENTREE_MCP_FILES to save the attachment to (default: that directory).

    Returns:
        JSON string with attachment data or {"error": "..."}.
    """
    c = await get_client()

    if operation == "list":
        return _json(await c.attachment_list(model_type, model_id))

    elif operation == "upload":
        return _json(await c.attachment_upload(model_type, model_id, file_path, comment))

    elif operation == "upload_link":
        return _json(await c.attachment_upload_link(model_type, model_id, link, comment))

    elif operation == "download":
        return _json(await c.attachment_download(attachment_id, destination))

    elif operation == "delete":
        return _json(await c.attachment_delete(attachment_id))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


@mcp.tool(
    annotations={
        "title": "InvenTree System Administration",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_safe("system")
async def system(
    operation: str,
    pk: int = None,
) -> str:
    """InvenTree system administration and health checks.

    Operations: health, version, settings, list_users, get_user,
    list_groups, list_owners, get_project_codes, list_currencies

    All operations are read-only.

    Args:
        operation: One of the operations listed above.
        pk: User ID for get_user.

    Returns:
        JSON string with system data.
    """
    c = await get_client()

    if operation == "health":
        return _json(await c.system_health())

    elif operation == "version":
        return _json(await c.system_version())

    elif operation == "settings":
        return _json(await c.system_settings())

    elif operation == "list_users":
        return _json(await c.system_list_users())

    elif operation == "get_user":
        return _json(await c.system_get_user(pk))

    elif operation == "list_groups":
        return _json(await c.system_list_groups())

    elif operation == "list_owners":
        return _json(await c.system_list_owners())

    elif operation == "get_project_codes":
        return _json(await c.system_get_project_codes())

    elif operation == "list_currencies":
        return _json(await c.system_list_currencies())

    else:
        return _json({"error": f"Unknown operation: {operation}"})


# ── DigiKey lookup (read-only, separate API) ──────────────────────────

_digikey = None


def get_digikey():
    """Get or create the DigiKey client singleton."""
    global _digikey
    if _digikey is None:
        from digikey import DigiKeyClient

        client_id = os.getenv("DIGIKEY_CLIENT_ID")
        client_secret = os.getenv("DIGIKEY_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError(
                "DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET must be set (env vars, .env, or ~/.config/inventree-mcp/.env)"
            )
        _digikey = DigiKeyClient(client_id, client_secret)
    return _digikey


@mcp.tool(
    annotations={
        "title": "DigiKey Part Lookup",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@_safe("digikey")
async def digikey(
    operation: str,
    query: str,
    limit: int = 10,
) -> str:
    """Look up parts on DigiKey (read-only; nothing is written to InvenTree).

    Operations:
      lookup: query = DigiKey product URL, DigiKey part number (e.g. 311-0.0HRCT-ND), or
        manufacturer part number. Returns manufacturer, MPN, description, category path,
        parameters, datasheet/photo/product URLs, stock, and per-packaging variations
        (DigiKey part number, minimum order quantity, price breaks).
      search: query = keywords (e.g. "10k 0603 resistor 1%"); returns up to `limit` short
        results to pick from, then call lookup on the chosen one.

    Adding a DigiKey part to InvenTree (use the returned values, never guess them):
      1. part.create with name/description from the lookup (category as the user wants).
      2. company "DigiKey" (is_supplier) and the manufacturer (is_manufacturer) must exist;
         create them if company.list doesn't find them.
      3. company.create_manufacturer_part: manufacturer, MPN = manufacturer_part_number.
      4. company.create_supplier_part: supplier = DigiKey, SKU = the variation's
         digikey_part_number (usually the Cut Tape one for small quantities),
         link = product_url, manufacturer_part, pack_quantity if relevant.
      5. company.add_price_break for each of that variation's price_breaks.
      6. Optional: part.set_parameter for parameters whose templates already exist.

    Args:
        operation: lookup or search.
        query: URL / part number for lookup, keywords for search.
        limit: Max results for search (default 10, max 50).

    Returns:
        JSON string with DigiKey product data or {"error": "..."}.
    """
    dk = get_digikey()

    if operation == "lookup":
        return _json(await dk.lookup(query))

    elif operation == "search":
        return _json(await dk.search(query, limit))

    else:
        return _json({"error": f"Unknown operation: {operation}"})


# ═══════════════════════════════════════════════════════════════════════
# Entry point — dual transport
# ═══════════════════════════════════════════════════════════════════════

def main():
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"

    if transport == "sse":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.getenv("PORT", "3074"))
        logger.info(f"Starting InvenTree MCP Server on SSE port {port}")
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        logger.info("Starting InvenTree MCP Server on STDIO")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
