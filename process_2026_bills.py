import sys
import xmlrpc.client
from collections import defaultdict
from datetime import datetime

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
print(f"✓ Company: PT Kultiva Indonesia Makmur (ID: {COMPANY_ID})\n")

# ==============================================================================
# STEP 1: Scan all POs (both 2026 and late 2025 with receipts in 2026)
# ==============================================================================
print("=" * 80)
print("STEP 1: Scanning Purchase Orders and Stock Pickings...")
print("=" * 80)

domain_all_pos = [
    ("company_id", "=", COMPANY_ID),
    ("state", "in", ["purchase", "done"])
]
all_pos = models.execute_kw(DB, uid, API_KEY, "purchase.order", "search_read", [domain_all_pos],
    {"fields": ["id", "name", "date_order", "partner_id", "invoice_status", "picking_ids", "invoice_ids"], "context": ctx}
)
po_by_name = {p["name"]: p for p in all_pos}
po_by_id = {p["id"]: p for p in all_pos}
print(f"Total confirmed/done POs loaded: {len(all_pos)}")

# Fetch all pickings for these POs
all_picking_ids = [pid for p in all_pos for pid in p["picking_ids"]]
pickings = models.execute_kw(DB, uid, API_KEY, "stock.picking", "search_read",
    [[("id", "in", all_picking_ids)]],
    {"fields": ["id", "name", "state", "date_done"], "context": ctx}
)
picking_dict = {pk["id"]: pk for pk in pickings}

po_done_receipts = {}
po_latest_receipt_date = {}

for p in all_pos:
    done_pks = [picking_dict[pid] for pid in p["picking_ids"] if pid in picking_dict and picking_dict[pid]["state"] == "done"]
    if done_pks:
        po_done_receipts[p["id"]] = done_pks
        # Sort by date_done
        dates = [pk["date_done"][:10] for pk in done_pks if pk.get("date_done")]
        po_latest_receipt_date[p["id"]] = max(dates) if dates else p["date_order"][:10]
    else:
        po_done_receipts[p["id"]] = []
        po_latest_receipt_date[p["id"]] = None

print(f"POs with at least one DONE receipt: {len(po_done_receipts)}")

# Find 2026 POs that have done receipts and need bill creation ('to invoice')
pos_to_create_bill = [
    p for p in all_pos 
    if p["date_order"][:4] == "2026" 
    and p["id"] in po_done_receipts 
    and len(po_done_receipts[p["id"]]) > 0
    and p["invoice_status"] == "to invoice"
]
print(f"2026 POs with DONE receipts needing bill creation: {len(pos_to_create_bill)}")

# ==============================================================================
# STEP 2: Create Bills for POs with Done Receipts that are 'to invoice'
# ==============================================================================
print("\n" + "=" * 80)
print("STEP 2: Creating vendor bills for POs with DONE receipts...")
print("=" * 80)

created_bill_ids = []
for i, p in enumerate(pos_to_create_bill, 1):
    po_id = p["id"]
    po_name = p["name"]
    partner_name = p["partner_id"][1] if p["partner_id"] else "Unknown"
    receipt_date = po_latest_receipt_date[po_id]
    
    try:
        # Pre-check invoice ids
        p_pre = models.execute_kw(DB, uid, API_KEY, "purchase.order", "read", [[po_id]], {"fields": ["invoice_ids"], "context": ctx})[0]
        pre_invs = p_pre["invoice_ids"]
        
        # Create invoice
        models.execute_kw(DB, uid, API_KEY, "purchase.order", "action_create_invoice", [[po_id]], {"context": ctx})
        
        # Post-check invoice ids to get newly created bill
        p_post = models.execute_kw(DB, uid, API_KEY, "purchase.order", "read", [[po_id]], {"fields": ["invoice_ids"], "context": ctx})[0]
        new_invs = [iid for iid in p_post["invoice_ids"] if iid not in pre_invs]
        
        if new_invs:
            for new_id in new_invs:
                # Update date and invoice_date to receipt_date
                models.execute_kw(DB, uid, API_KEY, "account.move", "write", [[new_id], {
                    "date": receipt_date,
                    "invoice_date": receipt_date
                }], {"context": ctx})
                created_bill_ids.append(new_id)
                print(f"  [{i}/{len(pos_to_create_bill)}] Created Bill ID {new_id} for PO {po_name} ({partner_name}) - Date: {receipt_date}")
        else:
            print(f"  [{i}/{len(pos_to_create_bill)}] PO {po_name}: action_create_invoice succeeded but no new invoice_id found.")
    except Exception as e:
        print(f"  [{i}/{len(pos_to_create_bill)}] ❌ Error creating bill for PO {po_name}: {e}")

print(f"✓ Total new bills created: {len(created_bill_ids)}")

# ==============================================================================
# STEP 3: Confirm and Pay Draft Bills with Done Receipts
# ==============================================================================
print("\n" + "=" * 80)
print("STEP 3: Confirming and Paying Vendor Bills with DONE Receipts...")
print("=" * 80)

# Fetch all draft 2026 bills
domain_drafts = [
    ("move_type", "=", "in_invoice"),
    ("company_id", "=", COMPANY_ID),
    ("state", "=", "draft"),
    ("date", ">=", "2026-01-01"),
    ("date", "<=", "2026-12-31")
]
draft_bills = models.execute_kw(DB, uid, API_KEY, "account.move", "search_read", [domain_drafts],
    {"fields": ["id", "name", "ref", "invoice_origin", "date", "invoice_date", "amount_total", "partner_id"], "context": ctx}
)
print(f"Total draft 2026 vendor bills currently: {len(draft_bills)}")

confirmed_count = 0
paid_count = 0
skipped_count = 0

for i, b in enumerate(draft_bills, 1):
    bill_id = b["id"]
    origin = b.get("invoice_origin") or ""
    po = po_by_name.get(origin)
    partner_name = b["partner_id"][1] if b["partner_id"] else "Unknown"
    
    # Check if bill has done receipt
    has_receipt = False
    receipt_date = None
    if po:
        p_id = po["id"]
        if p_id in po_done_receipts and len(po_done_receipts[p_id]) > 0:
            has_receipt = True
            receipt_date = po_latest_receipt_date[p_id]
            
    if not has_receipt:
        print(f"  [{i}/{len(draft_bills)}] Skipping Draft Bill ID {bill_id} (PO: '{origin}'): NO DONE RECEIPT")
        skipped_count += 1
        continue
        
    try:
        # Determine bill date:
        # If bill date is today (e.g. 2026-11-23) and receipt_date is available, adjust to receipt_date
        current_date = b.get("date")
        target_date = current_date
        if current_date == datetime.today().strftime("%Y-%m-%d") and receipt_date:
            target_date = receipt_date
            
        # Ensure invoice_date matches date
        write_vals = {}
        if b.get("date") != target_date:
            write_vals["date"] = target_date
        if b.get("invoice_date") != target_date:
            write_vals["invoice_date"] = target_date
            
        if write_vals:
            models.execute_kw(DB, uid, API_KEY, "account.move", "write", [[bill_id], write_vals], {"context": ctx})
            
        # Confirm / Post the bill
        models.execute_kw(DB, uid, API_KEY, "account.move", "action_post", [[bill_id]], {"context": ctx})
        confirmed_count += 1
        
        # Check if payment is needed
        b_posted = models.execute_kw(DB, uid, API_KEY, "account.move", "read", [[bill_id]],
            {"fields": ["id", "amount_total", "payment_state", "date"], "context": ctx}
        )[0]
        
        if b_posted["payment_state"] != "paid" and b_posted["amount_total"] > 0:
            pay_ctx = dict(ctx)
            pay_ctx.update({
                "active_model": "account.move",
                "active_ids": [bill_id]
            })
            wizard_vals = {
                "journal_id": 14,  # Bank journal
                "payment_date": b_posted["date"]
            }
            wizard_id = models.execute_kw(DB, uid, API_KEY, "account.payment.register", "create", [wizard_vals], {"context": pay_ctx})
            models.execute_kw(DB, uid, API_KEY, "account.payment.register", "action_create_payments", [[wizard_id]], {"context": pay_ctx})
            paid_count += 1
            print(f"  [{i}/{len(draft_bills)}] Bill ID {bill_id} (PO {origin}): Confirmed & Paid Rp {b_posted['amount_total']:,.0f} (Date: {b_posted['date']})")
        else:
            print(f"  [{i}/{len(draft_bills)}] Bill ID {bill_id} (PO {origin}): Confirmed (Amt: {b_posted['amount_total']}, State: {b_posted['payment_state']})")
    except Exception as e:
        print(f"  [{i}/{len(draft_bills)}] ❌ Error confirming/paying bill {bill_id}: {e}")

print(f"\n✓ Confirmed: {confirmed_count} bills")
print(f"✓ Paid: {paid_count} bills")
print(f"✓ Skipped (no done receipt): {skipped_count} bills")

# ==============================================================================
# STEP 4: Renumber all 2026 Posted Bills to BILL/2026/MM/INC
# ==============================================================================
print("\n" + "=" * 80)
print("STEP 4: Renumbering all 2026 Posted Vendor Bills...")
print("=" * 80)

domain_posted_2026 = [
    ("move_type", "=", "in_invoice"),
    ("company_id", "=", COMPANY_ID),
    ("date", ">=", "2026-01-01"),
    ("date", "<=", "2026-12-31"),
    ("state", "=", "posted")
]
posted_bills = models.execute_kw(DB, uid, API_KEY, "account.move", "search_read", [domain_posted_2026],
    {"fields": ["id", "name", "ref", "partner_id", "date", "amount_total", "payment_state"], "order": "date asc, id asc", "context": ctx}
)
print(f"Found {len(posted_bills)} posted 2026 vendor bills to renumber.")

# Group by month
monthly_bills = defaultdict(list)
for b in posted_bills:
    m_key = b["date"][:7]  # YYYY-MM
    monthly_bills[m_key].append(b)

plan = []
for m_key in sorted(monthly_bills.keys()):
    year, month = m_key.split("-")
    for inc, b in enumerate(monthly_bills[m_key], 1):
        target_name = f"BILL/{year}/{month}/{inc:04d}"
        plan.append({
            "id": b["id"],
            "old_name": b["name"],
            "new_name": target_name,
            "ref": b.get("ref"),
            "date": b["date"]
        })

print("\n--- Renumbering Plan Summary by Month ---")
for m_key in sorted(monthly_bills.keys()):
    print(f"  {m_key}: {len(monthly_bills[m_key])} bills (BILL/{m_key.replace('-', '/')}/0001 -> .../{len(monthly_bills[m_key]):04d})")

# Phase 1: Temporary names to prevent duplicate key constraint violations
print("\n⚡ Phase 1: Setting temporary names and preserving vendor refs...")
for item in plan:
    b_id = item["id"]
    old_name = item["old_name"]
    new_name = item["new_name"]
    curr_ref = item["ref"]
    
    if old_name != new_name:
        update_vals = {"name": f"TMP2026/{b_id}"}
        models.execute_kw(DB, uid, API_KEY, "account.move", "write", [[b_id], update_vals], {"context": ctx})

# Phase 2: Final sequential names
print("\n✨ Phase 2: Setting final BILL/YYYY/MM/INC names...")
for i, item in enumerate(plan, 1):
    b_id = item["id"]
    new_name = item["new_name"]
    models.execute_kw(DB, uid, API_KEY, "account.move", "write", [[b_id], {"name": new_name}], {"context": ctx})
    if i % 25 == 0 or i == len(plan):
        print(f"  Processed [{i}/{len(plan)}] bills (latest: {new_name})")

print(f"\n🎉 SUCCESS! Renumbered {len(plan)} vendor bills to BILL/2026/MM/INC format.")

# ==============================================================================
# STEP 5: Final Verification Report
# ==============================================================================
print("\n" + "=" * 80)
print("STEP 5: Final Verification Report")
print("=" * 80)

final_bills = models.execute_kw(DB, uid, API_KEY, "account.move", "search_read", [domain_posted_2026],
    {"fields": ["id", "name", "date", "invoice_date", "state", "payment_state", "amount_total"], "order": "date asc, name asc", "context": ctx}
)

m_counts = defaultdict(lambda: {"posted": 0, "paid": 0, "first": "", "last": ""})
for b in final_bills:
    m = b["date"][:7]
    m_counts[m]["posted"] += 1
    if b["payment_state"] == "paid":
        m_counts[m]["paid"] += 1
    if not m_counts[m]["first"]:
        m_counts[m]["first"] = b["name"]
    m_counts[m]["last"] = b["name"]

print(f"{'Month':<10} | {'Posted':<8} | {'Paid':<8} | {'First Number':<22} | {'Last Number':<22}")
print("-" * 78)
for m in sorted(m_counts.keys()):
    info = m_counts[m]
    print(f"{m:<10} | {info['posted']:<8} | {info['paid']:<8} | {info['first']:<22} | {info['last']:<22}")

print("-" * 78)
total_posted = sum(info["posted"] for info in m_counts.values())
total_paid = sum(info["paid"] for info in m_counts.values())
print(f"{'TOTAL':<10} | {total_posted:<8} | {total_paid:<8}")
