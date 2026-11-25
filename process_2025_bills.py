import sys
import xmlrpc.client

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

URL = "https://kultiva.odoo.com"
DB = "kultiva"
USERNAME = "finance@kultivaco.com"
API_KEY = "5d47ed0bdccfd1c7f313645a81a84ad9147e4162"
COMPANY_ID = 1  # PT Kultiva Indonesia Makmur

common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
uid = common.authenticate(DB, USERNAME, API_KEY, {})
if not uid:
    print("❌ Failed to authenticate with Odoo!")
    sys.exit(1)

models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)
ctx = {"allowed_company_ids": [COMPANY_ID], "company_id": COMPANY_ID}

print(f"✓ Authenticated as {USERNAME} (UID: {uid})")
print(f"🏢 Company: PT Kultiva Indonesia Makmur (ID: {COMPANY_ID})")

# 1. Search all active 2025 vendor bills (draft & posted)
domain = [
    ("move_type", "=", "in_invoice"),
    ("company_id", "=", COMPANY_ID),
    ("date", ">=", "2025-01-01"),
    ("date", "<=", "2025-12-31"),
    ("state", "in", ["draft", "posted"])
]

fields = ["id", "name", "ref", "partner_id", "date", "invoice_date", "state", "payment_state", "amount_total"]
bills = models.execute_kw(DB, uid, API_KEY, "account.move", "search_read", [domain], {"fields": fields, "order": "date asc, id asc", "context": ctx})

print(f"📋 Found {len(bills)} active vendor bills for 2025 to process.\n")

success_count = 0
date_updated_count = 0
posted_count = 0
paid_count = 0
error_count = 0

for i, b in enumerate(bills, 1):
    b_id = b["id"]
    acc_date = b["date"]
    inv_date = b.get("invoice_date")
    state = b["state"]
    pay_state = b.get("payment_state")
    amount = b.get("amount_total", 0.0)
    p_name = b["partner_id"][1] if b.get("partner_id") else "Unknown"
    ref = b.get("ref") or b.get("name") or str(b_id)

    print(f"[{i}/{len(bills)}] Bill ID {b_id} ({ref} - {p_name}):")
    print(f"    Current: state={state}, payment_state={pay_state}, date={acc_date}, invoice_date={inv_date}, amount={amount:,.2f}")

    try:
        # Step A: Update invoice_date = date if missing or different
        if not inv_date or inv_date != acc_date:
            models.execute_kw(DB, uid, API_KEY, "account.move", "write", [[b_id], {"invoice_date": acc_date}], {"context": ctx})
            date_updated_count += 1
            print(f"    ✓ Updated bill date (invoice_date) to match accounting date: {acc_date}")

        # Step B: If draft, post the bill
        if state == "draft":
            models.execute_kw(DB, uid, API_KEY, "account.move", "action_post", [[b_id]], {"context": ctx})
            posted_count += 1
            print(f"    ✓ Posted bill to ledger")

        # Refresh bill state after posting
        b_refreshed = models.execute_kw(DB, uid, API_KEY, "account.move", "read", [b_id], {"fields": ["state", "payment_state", "amount_total"], "context": ctx})[0]

        # Step C: If amount > 0 and payment_state != 'paid', register payment
        if b_refreshed["payment_state"] != "paid" and b_refreshed["amount_total"] > 0:
            wiz_ctx = {
                "active_model": "account.move",
                "active_ids": [b_id],
                "allowed_company_ids": [COMPANY_ID],
                "company_id": COMPANY_ID
            }
            wiz_id = models.execute_kw(
                DB, uid, API_KEY,
                "account.payment.register", "create",
                [{"journal_id": 14, "payment_date": acc_date}],  # Journal 14: Bank
                {"context": wiz_ctx}
            )
            models.execute_kw(
                DB, uid, API_KEY,
                "account.payment.register", "action_create_payments",
                [[wiz_id]],
                {"context": wiz_ctx}
            )
            paid_count += 1
            print(f"    ✓ Registered payment (Bank) on {acc_date} -> Marked as PAID")
        elif b_refreshed["payment_state"] == "paid":
            print(f"    ✓ Bill is already PAID (amount: {b_refreshed['amount_total']:,.2f})")

        success_count += 1

    except Exception as e:
        error_count += 1
        print(f"    ❌ Error processing Bill {b_id}: {e}")

print("\n" + "=" * 70)
print(" 📊 SUMMARY OF 2025 VENDOR BILLS UPDATE:")
print("=" * 70)
print(f"  • Total Bills Processed       : {len(bills)}")
print(f"  • Successfully Updated        : {success_count}")
print(f"  • Bill Dates (invoice_date) Set: {date_updated_count}")
print(f"  • Draft Bills Posted          : {posted_count}")
print(f"  • Payments Registered (Paid)  : {paid_count}")
print(f"  • Errors                      : {error_count}")
print("=" * 70)
