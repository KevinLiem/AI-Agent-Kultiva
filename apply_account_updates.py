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

# Target Account IDs
ACC_BANK = 155        # 11120005 Bank
ACC_PARKIR = 141        # 69000000 Other Expenses
ACC_MAINTENANCE = 131   # 65110080 Asset Maintenance Costs
ACC_PETTY_CASH = 49     # 11110010 Petty Cash
ACC_MAKAN_LEMBUR = 141  # 69000000 Other Expenses
ACC_SALES = 110         # 41000010 Sales
ACC_RECEIVABLE = 55     # 11210010 Account Receivable
ACC_PAYABLE = 75        # 21100010 Trade Receivable (Account Payable)
ACC_EQUIPMENT = 121     # 63110120 Office Equipment
ACC_RAW_MATERIAL = 101  # 29000000 Interim Stock
ACC_ATK = 71            # 12210030 Office Supplies
ACC_TAXES = 130         # 65110070 Taxes
ACC_SHIPPING = 132      # 65110090 Shipping Costs
ACC_OTHER_EXP = 141     # 69000000 Other Expenses

# ==============================================================================
# STEP 1: Update Product Category Defaults for Future Bills
# ==============================================================================
print("=" * 80)
print("STEP 1: Updating Product Category Default Expense Accounts...")
print("=" * 80)

categ_mappings = [
    (6, ACC_RAW_MATERIAL, "Raw Materials -> Interim Stock"),
    (13, ACC_RAW_MATERIAL, "Packaging Material (Primary/Direct) -> Interim Stock"),
    (14, ACC_RAW_MATERIAL, "Packaging Material (Secondary/Tertiary) -> Interim Stock"),
    (9, ACC_RAW_MATERIAL, "Sub Material -> Interim Stock"),
    (11, ACC_RAW_MATERIAL, "Sub Material -> Interim Stock"),
    (10, ACC_RAW_MATERIAL, "Semi Finished Goods / WIP -> Interim Stock"),
    (12, ACC_EQUIPMENT, "Peralatan -> Office Equipment"),
    (3, ACC_OTHER_EXP, "Expenses -> Other Expenses"),
    (16, ACC_MAINTENANCE, "Services -> Asset Maintenance Costs"),
]

for cat_id, acc_id, desc in categ_mappings:
    try:
        models.execute_kw(DB, uid, API_KEY, "product.category", "write",
            [[cat_id], {"property_account_expense_categ_id": acc_id}],
            {"context": ctx}
        )
        print(f"  ✓ Updated Category ID {cat_id}: {desc}")
    except Exception as e:
        print(f"  ❌ Failed to update Category ID {cat_id}: {e}")

# ==============================================================================
# STEP 2: Fetch and Classify All Vendor Bill Lines
# ==============================================================================
print("\n" + "=" * 80)
print("STEP 2: Classifying Vendor Bill Lines...")
print("=" * 80)

domain_moves = [("move_type", "=", "in_invoice"), ("company_id", "=", COMPANY_ID)]
moves = models.execute_kw(DB, uid, API_KEY, "account.move", "search_read", [domain_moves],
    {"fields": ["id", "name", "ref", "partner_id", "invoice_origin", "date"], "context": ctx}
)
move_map = {m["id"]: m for m in moves}

domain_lines = [
    ("move_id.move_type", "=", "in_invoice"),
    ("company_id", "=", COMPANY_ID),
    ("display_type", "=", "product")
]
lines = models.execute_kw(DB, uid, API_KEY, "account.move.line", "search_read", [domain_lines],
    {"fields": ["id", "move_id", "name", "product_id", "account_id", "price_subtotal"], "context": ctx}
)

prod_ids = list(set([l["product_id"][0] for l in lines if l["product_id"]]))
prods = models.execute_kw(DB, uid, API_KEY, "product.product", "read", [prod_ids],
    {"fields": ["id", "name", "categ_id"], "context": ctx}
)
prod_map = {p["id"]: p for p in prods}

def classify_line_final(l):
    m = move_map.get(l["move_id"][0], {})
    m_name = str(m.get("name") or "").lower()
    m_ref = str(m.get("ref") or "").lower()
    p_name = str(m.get("partner_id", ["", ""])[1] if m.get("partner_id") else "").lower()
    l_name = str(l.get("name") or "").lower()
    prod = prod_map.get(l["product_id"][0], {}) if l["product_id"] else {}
    categ_name = str(prod.get("categ_id", ["", ""])[1] if prod.get("categ_id") else "").lower()
    
    text = f"{l_name} {m_name} {m_ref} {p_name} {categ_name}"
    curr_acc = l["account_id"][0] if l["account_id"] else 0
    
    is_po_product = any(k in l_name for k in [
        "pouch", "tempeh", "chips", "melinjo", "banana", "seasoning", "bumbu",
        "tapioka", "tepung", "sabun", "sanitizer", "mt klin", "oxymove", "pallet",
        "gas tabung", "karton", "box", "lakban", "opp tape", "anbac", "minyak", "oil"
    ]) or "p00" in l_name or (m.get("invoice_origin") and m.get("invoice_origin").startswith("P00"))
    
    # 0. OOCL Expenses -> Bank (155)
    if "oocl" in text or "oocl" in p_name:
        return ACC_BANK, "0. OOCL Expenses -> Bank"

    # 1. Parkir / Parking / E-toll -> Other Expenses (141)
    if any(k in text for k in ["parkir", "parking", "e-toll", "tkkdm"]):
        return ACC_PARKIR, "1. Biaya parkir -> Other Expenses"
        
    # 2. Service Mesin / Kendaraan / Maintenance -> Asset Maintenance Costs (131)
    if any(k in text for k in [
        "service mesin", "service kendaraan", "servis mesin", "servis kendaraan",
        "spooring", "servis mobil", "service mobil", "perbaikan mesin",
        "pest management", "pesti plus", "pest extermination", "cctv", "pabx",
        "ganti oli", "oli mesin", "oli filter", "air aki", "switch power central",
        "mutasi ban", "shell helix", "arua service", "autopit selamat", "dipo internasional"
    ]) and not is_po_product:
        return ACC_MAINTENANCE, "2. Service Mesin / Kendaraan -> Asset Maintenance Costs"
        
    # 3. Petty Cash / Kas Kecil -> Petty Cash (49)
    if curr_acc == ACC_PETTY_CASH or any(k in text for k in ["petty cash", "kas kecil", "reimburse lia", "bongkar", "checker", "forklift", "pos keluar", "bill/adm"]):
        return ACC_PETTY_CASH, "3. Petty cash -> Petty cash"
        
    # 4. Makan Lembur / Overtime Meals -> Other Expenses (141)
    is_food_vendor = any(k in p_name for k in ["makan lembur", "resto", "warung", "catering", "d'besto", "geprek", "bakso"])
    is_food_item = any(k in l_name for k in [
        "bakso", "nasi", "mcd", "wingstop", "pho ma", "dada goreng", "paha goreng",
        "ayam dada", "ayam paha", "rendang", "makan lembur", "konsumsi"
    ])
    if (is_food_vendor or is_food_item) and not is_po_product:
        return ACC_MAKAN_LEMBUR, "4. Makan lembur -> Other Expenses"
        
    # 5. BKM / Transaksi lunas -> Sales (110)
    if any(k in text for k in ["bkm", "bukti kas masuk", "transaksi lunas"]):
        return ACC_SALES, "5. Transaksi lunas (BKM) -> Sales"
        
    # 9. Beli Peralatan Kantor -> Office Equipment (121)
    if any(k in l_name for k in [
        "peralatan kantor", "office equipment", "kran angsa", "metal detector",
        "printer", "laptop", "komputer", "meja kantor", "kursi kantor", "rak woh snack"
    ]) or "peralatan" in categ_name:
        return ACC_EQUIPMENT, "9. Beli peralatan kantor -> Office Equipment"
        
    # 11. Perlengkapan (ATK) -> Office Supplies (71)
    if any(k in l_name for k in ["atk", "alat tulis", "kertas a4", "pulpen", "spidol", "buku tulis"]):
        return ACC_ATK, "11. Perlengkapan (ATK) -> Office Supplies"
        
    # Taxes -> Taxes (130)
    if any(k in l_name for k in ["sales tax", "ppn", "faktur pajak", "vat"]):
        return ACC_TAXES, "Taxes -> Taxes"
        
    # Shipping -> Shipping Costs (132)
    if any(k in l_name for k in ["biaya pengiriman", "j&t", "ongkos kirim"]):
        return ACC_SHIPPING, "Shipping -> Shipping Costs"
        
    # Misc Expenses -> Other Expenses (141)
    if any(k in l_name for k in ["mcu karyawan", "bensin, flazz", "bahan bangunan", "tb halim sakbi"]):
        return ACC_OTHER_EXP, "Misc Expenses -> Other Expenses"
        
    # 10. Raw Material / Packaging / Inventory
    if curr_acc in [58, 101]:
        return curr_acc, "10. Raw Material -> Interim Stock / Other Inventory"
    return ACC_RAW_MATERIAL, "10. Raw Material -> Interim Stock / Other Inventory"

# Group updates by target account for batch writing
updates_by_account = defaultdict(list)
rule_counts = defaultdict(int)

for l in lines:
    target_acc, rule = classify_line_final(l)
    curr_acc = l["account_id"][0] if l["account_id"] else 0
    if curr_acc != target_acc:
        updates_by_account[target_acc].append(l["id"])
        rule_counts[rule] += 1

total_to_update = sum(len(ids) for ids in updates_by_account.values())
print(f"Total lines analyzed: {len(lines)}")
print(f"Total lines requiring account change: {total_to_update}")
print(f"Total lines already correct: {len(lines) - total_to_update}\n")

for rule, count in sorted(rule_counts.items(), key=lambda x: -x[1]):
    print(f"  {rule:60s}: {count:4d} lines")

# ==============================================================================
# STEP 3: Apply Account Updates in Batches
# ==============================================================================
print("\n" + "=" * 80)
print("STEP 3: Applying Account Updates in Batches...")
print("=" * 80)

BATCH_SIZE = 50
applied_total = 0

for target_acc, line_ids in updates_by_account.items():
    print(f"\nUpdating {len(line_ids)} lines to Account ID {target_acc}...")
    for i in range(0, len(line_ids), BATCH_SIZE):
        chunk = line_ids[i:i + BATCH_SIZE]
        try:
            models.execute_kw(DB, uid, API_KEY, "account.move.line", "write",
                [chunk, {"account_id": target_acc}],
                {"context": ctx}
            )
            applied_total += len(chunk)
            print(f"  ✓ Updated batch {i + 1}-{min(i + BATCH_SIZE, len(line_ids))} of {len(line_ids)}")
        except Exception as e:
            print(f"  ❌ Error updating batch {i + 1}-{min(i + BATCH_SIZE, len(line_ids))}: {e}")

print(f"\n🎉 SUCCESS! Applied account updates to {applied_total} / {total_to_update} lines.")

# ==============================================================================
# STEP 4: Final Verification of Chart of Accounts Distribution
# ==============================================================================
print("\n" + "=" * 80)
print("STEP 4: Final Verification Report...")
print("=" * 80)

final_lines = models.execute_kw(DB, uid, API_KEY, "account.move.line", "search_read", [domain_lines],
    {"fields": ["id", "account_id"], "context": ctx}
)

final_dist = defaultdict(int)
for l in final_lines:
    acc_name = l["account_id"][1] if l["account_id"] else "None"
    final_dist[acc_name] += 1

print(f"{'Account Code & Name':<50} | {'Total Lines':<12}")
print("-" * 65)
for acc_name, count in sorted(final_dist.items(), key=lambda x: -x[1]):
    print(f"{acc_name:<50} | {count:<12}")
print("-" * 65)
print(f"{'TOTAL':<50} | {len(final_lines):<12}")
