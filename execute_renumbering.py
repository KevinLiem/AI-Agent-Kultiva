import sys
import xmlrpc.client
from collections import defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

URL = "https://kultiva.odoo.com"
DB = "kultiva"
USERNAME = "finance@kultivaco.com"
API_KEY = "5d47ed0bdccfd1c7f313645a81a84ad9147e4162"
COMPANY_ID = 1

common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
uid = common.authenticate(DB, USERNAME, API_KEY, {})
if not uid:
    print("❌ Failed to authenticate with Odoo!")
    sys.exit(1)

models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)
ctx = {"allowed_company_ids": [COMPANY_ID], "company_id": COMPANY_ID}

print(f"✓ Connected to Odoo as {USERNAME} (UID: {uid})")

# 1. Fetch all posted 2025 vendor bills for Kultiva
domain = [
    ("move_type", "=", "in_invoice"),
    ("company_id", "=", COMPANY_ID),
    ("date", ">=", "2025-01-01"),
    ("date", "<=", "2025-12-31"),
    ("state", "=", "posted")
]

fields = ["id", "name", "ref", "partner_id", "date", "amount_total"]
bills = models.execute_kw(DB, uid, API_KEY, "account.move", "search_read", [domain], {"fields": fields, "order": "date asc, id asc", "context": ctx})

print(f"📋 Found {len(bills)} posted 2025 vendor bills to format as BILL/YYYY/MM/INC.\n")

# 2. Group by month and generate plan
monthly_bills = defaultdict(list)
for b in bills:
    month_key = b["date"][:7]  # e.g. "2025-01"
    monthly_bills[month_key].append(b)

plan = []
for month_key in sorted(monthly_bills.keys()):
    year, month = month_key.split("-")
    for inc, b in enumerate(monthly_bills[month_key], 1):
        target_name = f"BILL/{year}/{month}/{inc:04d}"
        plan.append({
            "id": b["id"],
            "old_name": b["name"],
            "new_name": target_name,
            "ref": b.get("ref"),
            "date": b["date"]
        })

# Phase 1: Set temporary names to prevent any unique constraint conflicts during renumbering
print("⚡ Phase 1: Preparing temporary sequences and preserving vendor refs...")
for item in plan:
    b_id = item["id"]
    old_name = item["old_name"]
    new_name = item["new_name"]
    curr_ref = item["ref"]

    if old_name != new_name:
        update_vals = {"name": f"TMP2025/{b_id}"}
        # If ref is empty and old_name was an actual vendor invoice number, preserve it in ref
        if not curr_ref and not old_name.startswith("BILL/") and old_name != "/":
            update_vals["ref"] = old_name
        models.execute_kw(DB, uid, API_KEY, "account.move", "write", [[b_id], update_vals], {"context": ctx})

# Phase 2: Assign final sequential BILL/YYYY/MM/INC numbers
print("\n✨ Phase 2: Assigning final BILL/YYYY/MM/INC numbers...")
renamed_count = 0
for i, item in enumerate(plan, 1):
    b_id = item["id"]
    new_name = item["new_name"]
    models.execute_kw(DB, uid, API_KEY, "account.move", "write", [[b_id], {"name": new_name}], {"context": ctx})
    renamed_count += 1
    if i % 25 == 0 or i == len(plan):
        print(f"  Processed [{i}/{len(plan)}] bills (latest: {new_name})")

print(f"\n🎉 SUCCESS! Renamed {renamed_count} vendor bills to BILL/YYYY/MM/INC format.")
