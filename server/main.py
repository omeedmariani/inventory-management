from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

class RestockRecommendation(BaseModel):
    item_sku: str
    item_name: str
    category: str
    forecasted_demand: int
    current_stock: int
    stock_gap: int
    recommended_qty: int
    unit_cost: float
    line_cost: float
    trend: str
    lead_time_days: int

class RestockingOrderItem(BaseModel):
    item_sku: str
    item_name: str
    category: str
    quantity: int
    unit_cost: float
    line_cost: float
    lead_time_days: int

class RestockingOrder(BaseModel):
    id: str
    order_number: str
    items: List[RestockingOrderItem]
    total_value: float
    budget: float
    status: str
    submitted_at: str
    expected_delivery: str
    lead_time_days: int

class CreateRestockingOrderRequest(BaseModel):
    budget: float
    items: List[RestockingOrderItem]


# --- Restocking helpers -------------------------------------------------------

# Demand-forecast SKUs don't all exist in inventory.json; map missing ones to a
# real category so per-category lead-time and unit-cost lookups stay consistent.
CATEGORY_LEAD_TIME_DAYS = {
    "Circuit Boards": 7,
    "Sensors": 5,
    "Actuators": 10,
    "Controllers": 12,
    "Power Supplies": 6,
}

CATEGORY_DEFAULT_COST = {
    "Circuit Boards": 24.99,
    "Sensors": 14.50,
    "Actuators": 89.00,
    "Controllers": 110.00,
    "Power Supplies": 18.99,
}

SKU_PREFIX_CATEGORY = {
    "PCB": "Circuit Boards",
    "SNR": "Sensors",
    "MTR": "Actuators",
    "VLV": "Actuators",
    "CTL": "Controllers",
    "PSU": "Power Supplies",
    "WDG": "Actuators",
    "BRG": "Actuators",
    "GSK": "Sensors",
    "FLT": "Sensors",
}

_inventory_by_sku = {item["sku"]: item for item in inventory_items}
restocking_orders: list[dict] = []


def _category_for_sku(sku: str) -> str:
    inv = _inventory_by_sku.get(sku)
    if inv:
        return inv["category"]
    prefix = sku.split("-")[0]
    return SKU_PREFIX_CATEGORY.get(prefix, "Controllers")


def _unit_cost_for_sku(sku: str, category: str) -> float:
    inv = _inventory_by_sku.get(sku)
    if inv:
        return inv["unit_cost"]
    return CATEGORY_DEFAULT_COST.get(category, 50.0)


def _build_recommendations() -> list[dict]:
    recs = []
    for f in demand_forecasts:
        sku = f["item_sku"]
        inv = _inventory_by_sku.get(sku)
        category = _category_for_sku(sku)
        current_stock = inv["quantity_on_hand"] if inv else f.get("current_demand", 0)
        gap = f["forecasted_demand"] - current_stock
        recommended_qty = max(gap, 0)
        unit_cost = _unit_cost_for_sku(sku, category)
        lead_time = CATEGORY_LEAD_TIME_DAYS.get(category, 7)
        recs.append({
            "item_sku": sku,
            "item_name": f["item_name"],
            "category": category,
            "forecasted_demand": f["forecasted_demand"],
            "current_stock": current_stock,
            "stock_gap": gap,
            "recommended_qty": recommended_qty,
            "unit_cost": unit_cost,
            "line_cost": round(recommended_qty * unit_cost, 2),
            "trend": f.get("trend", "stable"),
            "lead_time_days": lead_time,
        })
    recs.sort(key=lambda r: r["stock_gap"], reverse=True)
    return recs

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports():
    """Get quarterly performance reports"""
    # Calculate quarterly statistics from orders
    quarters = {}

    for order in orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends():
    """Get month-over-month trends"""
    months = {}

    for order in orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

@app.get("/api/restocking/recommendations", response_model=List[RestockRecommendation])
def get_restocking_recommendations():
    """Items recommended for restocking, ranked by stock gap (forecast - current)."""
    return _build_recommendations()


@app.post("/api/restocking/orders", response_model=RestockingOrder)
def create_restocking_order(req: CreateRestockingOrderRequest):
    """Submit a restocking order; persisted in-memory until server restart."""
    if not req.items:
        raise HTTPException(status_code=400, detail="At least one item is required")

    items = []
    for it in req.items:
        category = it.category or _category_for_sku(it.item_sku)
        lead = CATEGORY_LEAD_TIME_DAYS.get(category, 7)
        items.append({
            "item_sku": it.item_sku,
            "item_name": it.item_name,
            "category": category,
            "quantity": it.quantity,
            "unit_cost": it.unit_cost,
            "line_cost": round(it.quantity * it.unit_cost, 2),
            "lead_time_days": lead,
        })

    total_value = round(sum(i["line_cost"] for i in items), 2)
    order_lead = max(i["lead_time_days"] for i in items)
    now = datetime.now()
    order_id = str(len(restocking_orders) + 1)
    order = {
        "id": order_id,
        "order_number": f"RST-{now.year}-{int(order_id):04d}",
        "items": items,
        "total_value": total_value,
        "budget": req.budget,
        "status": "Submitted",
        "submitted_at": now.isoformat(timespec="seconds"),
        "expected_delivery": (now + timedelta(days=order_lead)).isoformat(timespec="seconds"),
        "lead_time_days": order_lead,
    }
    restocking_orders.append(order)
    return order


@app.get("/api/restocking/orders", response_model=List[RestockingOrder])
def get_restocking_orders():
    """All submitted restocking orders (newest first)."""
    return list(reversed(restocking_orders))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
