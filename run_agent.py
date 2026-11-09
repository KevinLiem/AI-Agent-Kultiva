import os
import sys
import re
import json
import glob
import xmlrpc.client
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# --- CONFIGURATION ---
if os.path.exists(".env"):
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

URL = os.getenv("ODOO_URL", "https://kultiva.odoo.com")
DB = os.getenv("ODOO_DB", "kultiva")
USERNAME = os.getenv("ODOO_USERNAME", "ppicjkt@kultivaco.com")
API_KEY = os.getenv("ODOO_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Ensure all Astro PO orders are created under PT Alpha Mas Indo (Company ID: 8)
TARGET_COMPANY_ID = 8
TARGET_COMPANY_NAME = "PT Alpha Mas Indo"
ODOO_CONTEXT = {"allowed_company_ids": [TARGET_COMPANY_ID, 1], "company_id": TARGET_COMPANY_ID}

# --- REFERENCE MAPPING FOR UNMATCHED / CUSTOM BARCODES & ALIASES ---
PRODUCT_REFERENCE_MAP = {
    # Customer Barcodes -> Exact Odoo Product Name
    "10000000005755": "[ASTRO] Tempeh Chips Nusantara Beef Rendang 60g",
    "10000000005756": "[ASTRO] Tempeh Chips Nusantara Gulai Ayam 60g",
    "8997219061330": "[ASTRO GOODS] Tempe Black Truffle 60gr",
    "8997219061347": "[ASTRO GOODS] Tempe Sour Cream 60gr",
    "8997231800092": "[ASTRO GOODS] Tempe Original 60gr",
    "8997219061712": "[ASTRO GOODS] Tempe Seaweed 60gr",
    "8997219061941": "[ASTRO GOODS] Tempe Black Truffle 250gr",
    "8997219061989": "[ASTRO GOODS] Tempe Mala 60gr",

    # Name Keywords / Aliases -> Exact Odoo Product Name
    "rendang": "[ASTRO] Tempeh Chips Nusantara Beef Rendang 60g",
    "gulai": "[ASTRO] Tempeh Chips Nusantara Gulai Ayam 60g",
    "beef rendang": "[ASTRO] Tempeh Chips Nusantara Beef Rendang 60g",
    "gulai ayam": "[ASTRO] Tempeh Chips Nusantara Gulai Ayam 60g",
}

def get_image_path():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        return sys.argv[1]
    if os.path.exists("astro615196.jpeg"):
        return "astro615196.jpeg"
    images = glob.glob("*.jpeg") + glob.glob("*.jpg") + glob.glob("*.png")
    if images:
        images.sort(key=os.path.getmtime, reverse=True)
        return images[0]
    return "WhatsApp Image 2026-11-04 at 10.49.17 AM.jpeg"

# --- STEP 1: DEFINE EXTRACTION SCHEMA ---
class PurchaseOrderItem(BaseModel):
    kode_barang: str = Field(description="Barcode or item code (Kode Barang)")
    nama_barang: str = Field(description="Product name (Nama Barang)")
    quantity: float = Field(description="Quantity (Kts)")
    uom: str = Field(description="Unit of Measure, e.g., PCS")
    unit_price: float | None = Field(default=None, description="Unit price (@Harga)")

class PurchaseOrderExtraction(BaseModel):
    po_number: str = Field(description="Purchase order number (Nomor)")
    po_date: str = Field(description="Date of PO (Tanggal)")
    shipping_date: str = Field(description="Shipping/Delivery date (Tanggal Kirim)")
    customer_name: str = Field(description="Customer company name that issued the PO (the buyer in the document header, e.g. PT Astro Indokarya Semesta; NOT the vendor in Kepada)")
    delivery_warehouse: str | None = Field(default=None, description="Delivery warehouse or location from delivery address (e.g., WH Sentul, WH Cibitung)")
    notes: str | None = Field(default=None, description="Delivery notes or remarks (Keterangan)")
    items: list[PurchaseOrderItem] = Field(description="List of items in the PO")

# --- HELPER: PRODUCT MATCHER ---
def match_product(models, uid, item, existing_products):
    # 1. Check Explicit Reference Map by Barcode
    barcode = str(item.get("kode_barang", "")).strip()
    if barcode in PRODUCT_REFERENCE_MAP:
        ref_name = PRODUCT_REFERENCE_MAP[barcode]
        for prod in existing_products:
            if prod["name"].strip().lower() == ref_name.strip().lower():
                return prod["id"]

    # 2. Check Odoo Barcode field
    if barcode:
        product_ids = models.execute_kw(
            DB, uid, API_KEY,
            "product.product", "search",
            [[["barcode", "=", barcode]]],
            {"context": ODOO_CONTEXT}
        )
        if product_ids:
            return product_ids[0]

    # 3. Check Explicit Reference Map by Name keyword
    item_name_lower = item["nama_barang"].lower()
    for kw, target_name in PRODUCT_REFERENCE_MAP.items():
        if kw in item_name_lower:
            for prod in existing_products:
                if prod["name"].strip().lower() == target_name.strip().lower():
                    return prod["id"]

    # 4. Token / Jaccard similarity fallback (excluding packaging pouches)
    def clean_tokens(text):
        t = text.lower().replace("[astro goods]", "").replace("[astro]", "").replace("astro goods", "")
        t = t.replace("chips", "").replace("tempeh", "tempe").replace("-", "")
        t = re.sub(r"(\d+)\s*g(r)?", r"\1gr", t)
        return set(re.findall(r"\w+", t))

    target_tokens = clean_tokens(item["nama_barang"])
    best_id, best_score = None, -1.0
    for prod in existing_products:
        p_tokens = clean_tokens(prod["name"])
        union = target_tokens.union(p_tokens)
        if not union:
            continue
        sim = len(target_tokens.intersection(p_tokens)) / len(union)
        if sim > best_score:
            best_score = sim
            best_id = prod["id"]

    if best_id and best_score >= 0.3:
        return best_id
    return None

def process_po_and_create_so(image_path=None, force_create=False):
    target_image = image_path or get_image_path()
    print(f"📄 Processing Image: {target_image}")

    # 1. Extract Data via Gemini
    print("🤖 Analyzing purchase order image with Gemini...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    with open(target_image, "rb") as f:
        image_bytes = f.read()

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            "Extract all purchase order details, customer name, delivery warehouse, and line items precisely from this document."
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=PurchaseOrderExtraction,
            temperature=0.1,
        ),
    )
    po_data = json.loads(response.text)
    print(f"✅ Extracted PO: {po_data['po_number']} for Customer: {po_data['customer_name']}")
    print(f"   Warehouse: {po_data.get('delivery_warehouse')} | Shipping Date: {po_data.get('shipping_date')}")

    # 2. Connect to Odoo via XML-RPC
    print(f"🔌 Connecting to Odoo models ({TARGET_COMPANY_NAME}, ID: {TARGET_COMPANY_ID})...")
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common")
    uid = common.authenticate(DB, USERNAME, API_KEY, {})
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")

    # Check for existing Sales Order in Alpha Mas Indo
    existing_sos = models.execute_kw(
        DB, uid, API_KEY,
        "sale.order", "search_read",
        [[["client_order_ref", "=", po_data["po_number"]], ["company_id", "=", TARGET_COMPANY_ID]]],
        {"fields": ["id", "name", "state", "amount_total", "company_id"], "order": "id desc", "context": ODOO_CONTEXT}
    )

    if existing_sos and not force_create:
        latest = existing_sos[0]
        print(f"\n⚠️  A Sales Order already exists in {TARGET_COMPANY_NAME} for PO {po_data['po_number']}:")
        for so in existing_sos:
            print(f"   - {so['name']} (ID: {so['id']}) | State: {so['state']} | Total: Rp {so['amount_total']:,.0f}")
        
        lines = models.execute_kw(
            DB, uid, API_KEY,
            "sale.order.line", "search_read",
            [[["order_id", "=", latest["id"]]]],
            {"fields": ["name", "product_uom_qty", "price_unit", "price_subtotal"], "context": ODOO_CONTEXT}
        )
        print(f"\n📦 Verified line items in {latest['name']}:")
        for l in lines:
            print(f"   • {l['name']} | Qty: {l['product_uom_qty']:,.0f} @ Rp {l['price_unit']:,.0f} = Rp {l['price_subtotal']:,.0f}")

        print(f"\n👉 Using existing order in {TARGET_COMPANY_NAME}: {latest['name']} (ID: {latest['id']})")
        return latest["id"]

    # 3. Find Customer & Warehouse (res.partner)
    clean_customer = re.sub(r"^(PT\.?|CV\.?)\s*", "", po_data["customer_name"], flags=re.IGNORECASE).strip()
    partner_ids = models.execute_kw(
        DB, uid, API_KEY,
        "res.partner", "search",
        [[["name", "ilike", clean_customer]]],
        {"context": ODOO_CONTEXT}
    )

    if partner_ids:
        partner_id = partner_ids[0]
        print(f"Found customer ID: {partner_id}")
    else:
        partner_id = models.execute_kw(
            DB, uid, API_KEY,
            "res.partner", "create",
            [{"name": po_data["customer_name"], "customer_rank": 1, "company_id": TARGET_COMPANY_ID}],
            {"context": ODOO_CONTEXT}
        )
        print(f"Created customer ID: {partner_id}")

    wh_raw = po_data.get("delivery_warehouse") or ""    
    wh_match = re.search(r"(Cibitung|Sentul|Kosambi)", wh_raw, re.IGNORECASE)
    wh_search = wh_match.group(1) if wh_match else wh_raw

    wh_partners = models.execute_kw(
        DB, uid, API_KEY,
        "res.partner", "search",
        [[["parent_id", "=", partner_id], ["name", "ilike", wh_search]]],
        {"context": ODOO_CONTEXT}
    )
    partner_shipping_id = wh_partners[0] if wh_partners else partner_id
    print(f"Partner shipping / order ID: {partner_shipping_id}")

    # 4. Map Line Items & Products (excluding raw packaging pouches)
    odoo_products = models.execute_kw(
        DB, uid, API_KEY,
        "product.product", "search_read",
        [[["sale_ok", "=", True], ["name", "not ilike", "Pouch "]]],
        {"fields": ["id", "name", "list_price", "barcode"], "context": ODOO_CONTEXT}
    )

    order_lines = []
    for item in po_data["items"]:
        product_id = match_product(models, uid, item, odoo_products)
        if not product_id:
            raise ValueError(f"Product not found in Odoo inventory: [{item['kode_barang']}] {item['nama_barang']}")

        product_rec = models.execute_kw(
            DB, uid, API_KEY,
            "product.product", "read",
            [product_id], {"fields": ["name", "list_price"], "context": ODOO_CONTEXT}
        )[0]

        unit_price = item.get("unit_price") or product_rec["list_price"]
        line_subtotal = item["quantity"] * unit_price
        order_lines.append((0, 0, {
            "product_id": product_id,
            "name": product_rec["name"],
            "product_uom_qty": item["quantity"],
            "price_unit": unit_price,
        }))
        print(f"  Mapped: {item['nama_barang']} -> {product_rec['name']} | Qty: {item['quantity']} @ Rp {unit_price:,.0f} = Rp {line_subtotal:,.0f}")

    # 5. Create Sales Order in Odoo explicitly for PT Alpha Mas Indo (company_id: 8)
    so_vals = {
        "company_id": TARGET_COMPANY_ID,
        "partner_id": partner_shipping_id,
        "partner_shipping_id": partner_shipping_id,
        "client_order_ref": po_data["po_number"],
        "note": f"Keterangan: {po_data.get('notes', '')} | Tanggal Kirim: {po_data['shipping_date']}",
        "order_line": order_lines,
    }

    sale_order_id = models.execute_kw(
        DB, uid, API_KEY,
        "sale.order", "create",
        [so_vals],
        {"context": ODOO_CONTEXT}
    )
    
    so_rec = models.execute_kw(
        DB, uid, API_KEY,
        "sale.order", "read",
        [sale_order_id], {"fields": ["name", "amount_total", "company_id"], "context": ODOO_CONTEXT}
    )[0]

    print(f"\n🎉 SUCCESS! Sales Order created: {so_rec['name']} (ID: {sale_order_id}) in {so_rec['company_id'][1]}")
    print(f"   Total Amount: Rp {so_rec['amount_total']:,.0f}")
    return sale_order_id

if __name__ == "__main__":
    process_po_and_create_so()
