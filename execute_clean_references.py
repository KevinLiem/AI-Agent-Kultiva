import sys
import xmlrpc.client
import re
from collections import defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

URL = "https://kultiva.odoo.com"
DB = "kultiva"
USERNAME = "finance@kultivaco.com"
API_KEY = "2c98c5c814dcb3cfd20e43d2991d9a35637dfe6f"
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

domain = [
    ("move_type", "=", "in_invoice"),
    ("company_id", "=", COMPANY_ID)
]
moves = models.execute_kw(DB, uid, API_KEY, "account.move", "search_read", [domain],
    {"fields": ["id", "name", "ref", "partner_id", "invoice_origin", "date"], "context": ctx}
)

def is_valid_invoice_code(ref):
    r = str(ref or "").strip()
    if not r or r.lower() == "false" or r == "/":
        return False, "empty"
    if "makan lembur" in r.lower():
        return False, "makan_lembur"
    lower = r.lower()
    descriptive_words = [
        "transfer", "dp ", "dp 50%", "dp 30%", "dp 20%", "pelunasan", "titip", "reimburse",
        "tambal ban", "ganti oli", "bahan bangunan", "evil bandara", "rak woh",
        "kirim ke", "dan", "box @", "kg", "pcs", "original", "mentah"
    ]
    if any(w in lower for w in descriptive_words):
        return False, "descriptive_phrase"
    if r.startswith("[") or r.startswith("{"):
        return False, "product_in_brackets"
    product_words = [
        "bumbu", "pouch", "kalog", "oxymove", "lakban", "gas", "tapioka", "tepung", "sabun",
        "fish skin", "tempe", "keripik", "banana", "pisang", "melinjo", "ubi", "sanitizer",
        "stiker", "sticker", "soligen", "cartridge"
    ]
    if any(pw in lower for pw in product_words):
        return False, "product_description"
    has_digit = any(c.isdigit() for c in r)
    is_code_pattern = bool(re.match(r"^[A-Za-z0-9\-\.\/_# ]+$", r)) and (has_digit or re.match(r"^P-\d+$", r, re.IGNORECASE))
    if is_code_pattern and len(r.split()) <= 4:
        return True, "valid_code"
    return False, "other_text"

to_clear_ids = []
makan_lembur_count = 0
other_clear_count = 0
keep_count = 0

for m in moves:
    ref = m.get("ref")
    if not ref:
        continue
    valid, reason = is_valid_invoice_code(ref)
    if not valid:
        to_clear_ids.append(m["id"])
        if reason == "makan_lembur":
            makan_lembur_count += 1
        else:
            other_clear_count += 1
    else:
        keep_count += 1

print(f"Total bills with existing reference: {len(to_clear_ids) + keep_count}")
print(f"  - Makan Lembur references to clear: {makan_lembur_count}")
print(f"  - Descriptive/product references to clear: {other_clear_count}")
print(f"  - Total to make empty: {len(to_clear_ids)}")
print(f"  - Valid invoice numbers/codes to preserve: {keep_count}\n")

# Execute clearing in batches of 50
BATCH_SIZE = 50
print("⚡ Clearing non-code references in batches...")
cleared_total = 0
for i in range(0, len(to_clear_ids), BATCH_SIZE):
    chunk = to_clear_ids[i:i + BATCH_SIZE]
    try:
        models.execute_kw(DB, uid, API_KEY, "account.move", "write",
            [chunk, {"ref": False}],
            {"context": ctx}
        )
        cleared_total += len(chunk)
        print(f"  ✓ Cleared batch {i + 1}-{min(i + BATCH_SIZE, len(to_clear_ids))} of {len(to_clear_ids)}")
    except Exception as e:
        print(f"  ❌ Error clearing batch: {e}")

print(f"\n🎉 Successfully cleared {cleared_total} references.")

# Verification Report
print("\n" + "=" * 80)
print("FINAL VERIFICATION REPORT")
print("=" * 80)

final_moves = models.execute_kw(DB, uid, API_KEY, "account.move", "search_read", [domain],
    {"fields": ["id", "name", "ref", "partner_id", "date"], "order": "date desc, id desc", "context": ctx}
)

final_with_ref = [m for m in final_moves if m.get("ref")]
final_makan_lembur = [m for m in final_moves if m.get("ref") and "makan lembur" in str(m.get("ref")).lower()]

print(f"Total vendor bills: {len(final_moves)}")
print(f"Bills with Makan Lembur in ref: {len(final_makan_lembur)} (Must be 0!)")
print(f"Bills with clean code/number ref: {len(final_with_ref)}")
print(f"Bills with empty ref: {len(final_moves) - len(final_with_ref)}\n")

print(f"{'Bill Number':<25} | {'Date':<12} | {'Partner':<30} | {'Clean Code / Number Ref':<25}")
print("-" * 100)
for m in final_with_ref:
    p = m.get("partner_id", ["", ""])[1] if m.get("partner_id") else ""
    print(f"{m['name']:<25} | {str(m.get('date')):<12} | {p[:30]:<30} | {str(m.get('ref')):<25}")
