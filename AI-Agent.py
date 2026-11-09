import os
import sys
import re
import json
import glob
import mimetypes
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

# --- IMAGE TO PROCESS ---
# Set this to a specific filename to override auto-detection, e.g. "astro.jpeg"
# Leave as None to automatically pick the most recently modified image in the folder.
IMAGE_PATH = "ASTRO615194.pdf"


def get_client_and_company(customer_name, items=None):
    """
    Determines company and client tag:
    - Astro and Fore are in Alpha Mas Indo (Company ID 8).
    - All other clients are in Kultiva Indonesia Makmur (Company ID 1).
    - Returns (company_id, company_name, client_tag)
    """
    name_upper = (customer_name or "").upper()
    
    # If customer was incorrectly parsed as our own fulfilling company, inspect items
    if "ALPHA MAS" in name_upper or "KULTIVA" in name_upper:
        if items:
            combined_items = " ".join([it.nama_barang.upper() for it in items])
            if "ASTRO" in combined_items:
                return 8, "PT Alpha Mas Indo", "ASTRO"
            elif "FORE" in combined_items:
                return 8, "PT Alpha Mas Indo", "FORE"
            elif "SEASTAR" in combined_items:
                return 1, "PT Kultiva Indonesia Makmur", "SEASTAR"
            elif "KRACKLES" in combined_items or "TIBICOS" in combined_items:
                return 1, "PT Kultiva Indonesia Makmur", "KRACKLES"
            elif "BRUNEI" in combined_items or "SAHABAT" in combined_items:
                return 1, "PT Kultiva Indonesia Makmur", "BRUNEI"

    if "ASTRO" in name_upper:
        return 8, "PT Alpha Mas Indo", "ASTRO"
    elif "FORE" in name_upper:
        return 8, "PT Alpha Mas Indo", "FORE"
    elif "SEASTAR" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "SEASTAR"
    elif "SANGLA" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "SANGLA"
    elif "HEALTHY" in name_upper or "HLSG" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "HEALTHY LIVING"
    elif "TIBICOS" in name_upper or "BODY ABSOLUTE" in name_upper or "KRACKLES" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "KRACKLES"
    elif "BRUNEI" in name_upper or "SAHABAT" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "BRUNEI"
    elif "EVIL" in name_upper or "EMBERZ" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "EVIL SNACKS"
    elif "RAVA" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "RAVA"
    elif "WOH" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "WOH"
    elif "SUNNANZ" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "SUNNANZ"
    elif "EXOTIQUE" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "EXOTIQUE"
    elif "DREIHA" in name_upper:
        return 1, "PT Kultiva Indonesia Makmur", "DREIHA"
    else:
        # Extract first substantive word as client tag
        words = re.findall(r"\b[A-Z]{3,}\b", name_upper)
        client_tag = None
        for w in words:
            if w not in ["THE", "LTD", "INC", "CORP", "FOODS", "FOOD", "INDONESIA", "MAKMUR", "PT", "CV", "ALPHA", "MAS", "INDO", "KULTIVA"]:
                client_tag = w
                break
        return 1, "PT Kultiva Indonesia Makmur", client_tag

def get_image_path():
    # 1. Command-line argument takes top priority
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        return sys.argv[1]
    # 2. Top-level IMAGE_PATH variable
    if IMAGE_PATH:
        return IMAGE_PATH
    # 3. Auto-pick the most recently modified image or PDF in the folder
    images = glob.glob("*.jpeg") + glob.glob("*.jpg") + glob.glob("*.png") + glob.glob("*.pdf")
    if images:
        images.sort(key=os.path.getmtime, reverse=True)
        return images[0]
    raise FileNotFoundError("No file found. Set IMAGE_PATH or pass an image/PDF as a command-line argument.")

# --- STEP 1: DEFINE EXTRACTION SCHEMA ---
class PurchaseOrderItem(BaseModel):
    kode_barang: str = Field(default="", description="Barcode or item code, if available")
    nama_barang: str = Field(description="Product name or description of goods")
    quantity: float = Field(description="Quantity in packs or units")
    uom: str = Field(default="PCS", description="Unit of Measure, e.g. PACKS or PCS")
    unit_price: float | None = Field(default=None, description="Unit price (@Harga), if available")

class PurchaseOrderExtraction(BaseModel):
    po_number: str = Field(description="Purchase order number (Nomor / Order No)")
    po_date: str = Field(description="Date of PO (Tanggal / Order Date)")
    shipping_date: str | None = Field(default=None, description="Shipping date / ETA date / Tanggal Kirim")
    customer_name: str = Field(description="Customer / buyer company name issuing the PO (e.g. PT Astro Indokarya Semesta, Seastar, Fore). NEVER use PT Alpha Mas Indo or PT Kultiva Indonesia Makmur as customer name.")
    delivery_warehouse: str | None = Field(default=None, description="Delivery warehouse / ship to location")
    notes: str | None = Field(default=None, description="Delivery notes or remarks")
    items: list[PurchaseOrderItem] = Field(description="List of items in the PO")

# --- HELPER: PRODUCT MATCHER (SCOPED TO SAME CLIENT) ---
def match_product(models, uid, item, existing_products, client_tag=None, odoo_context=None):
    """
    Matches products strictly ensuring products belong to the SAME CLIENT.
    Prevents cross-mapping (e.g. Seastar order getting Astro or Rava products).
    """
    barcode = str(item.get("kode_barang", "")).strip()

    # 1. Check Odoo barcode field
    if barcode:
        product_ids = models.execute_kw(
            DB, uid, API_KEY,
            "product.product", "search",
            [[["barcode", "=", barcode]]],
            {"context": odoo_context or {}}
        )
        if product_ids:
            return product_ids[0]

    # 2. Filter candidate products strictly to the same client
    candidate_products = existing_products
    if client_tag:
        tag_upper = client_tag.upper()
        # Find products matching the client tag in brackets or name
        client_candidates = [
            p for p in existing_products
            if tag_upper in p["name"].upper() or f"[{tag_upper}" in p["name"].upper()
        ]
        if client_candidates:
            candidate_products = client_candidates
        else:
            # If no client-specific products found, exclude products belonging to OTHER known clients
            other_brands = ["ASTRO", "SEASTAR", "RAVA", "FORE", "WOH", "SANGLA", "SUNNANZ", "EXOTIQUE", "DREIHA"]
            excluded_brands = [b for b in other_brands if b != tag_upper]
            candidate_products = [
                p for p in existing_products
                if not any(f"[{b}" in p["name"].upper() for b in excluded_brands)
            ]

    # 3. Token similarity matching within client products
    def clean_tokens(text):
        t = text.lower()
        if client_tag:
            t = t.replace(f"[{client_tag.lower()}]", "").replace(f"[{client_tag.lower()} goods]", "").replace(client_tag.lower(), "")
        t = t.replace("[astro goods]", "").replace("[astro]", "")
        t = t.replace("chips", "").replace("tempeh", "tempe").replace("-", "")
        t = re.sub(r"(\d+)\s*g(r)?", r"\1gr", t)
        return set(re.findall(r"\w+", t))

    target_tokens = clean_tokens(item["nama_barang"])
    best_id, best_score = None, -1.0
    for prod in candidate_products:
        p_tokens = clean_tokens(prod["name"])
        union = target_tokens.union(p_tokens)
        if not union:
            continue
        sim = len(target_tokens.intersection(p_tokens)) / len(union)
        if sim > best_score:
            best_score = sim
            best_id = prod["id"]

    if best_id and best_score >= 0.25:
        return best_id
    return None

def process_po_and_create_so(image_path=None, force_create=False):
    target_image = image_path or get_image_path()
    print(f"📄 Processing File: {target_image}")

    # 1. Extract Data via Gemini
    print("🤖 Analyzing purchase order image with Gemini...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    with open(target_image, "rb") as f:
        image_bytes = f.read()

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mimetypes.guess_type(target_image)[0] or "image/jpeg"),
            (
                "Extract all purchase order details precisely from this document. "
                "CRITICAL: The customer_name MUST be the BUYER company issuing the PO (e.g. PT Astro Indokarya Semesta, Seastar, Fore). "
                "DO NOT set PT Alpha Mas Indo or PT Kultiva Indonesia Makmur as the customer; they are our internal companies receiving/fulfilling the order."
            )
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=PurchaseOrderExtraction,
            temperature=0.1,
        ),
    )
    po_data = json.loads(response.text)
    print(f"✅ Extracted PO: {po_data['po_number']} for Customer: {po_data['customer_name']}")

    # 2. Determine Company & Client Tag
    items_objs = [PurchaseOrderItem(**it) for it in po_data.get("items", [])]
    target_company_id, target_company_name, client_tag = get_client_and_company(po_data["customer_name"], items=items_objs)
    print(f"🏢 Routing: Customer '{po_data['customer_name']}' -> {target_company_name} (ID: {target_company_id})")
    print(f"🏷️  Client Brand Scope: [{client_tag}] (Ensuring products are chosen strictly from this client)")

    odoo_context = {"allowed_company_ids": [target_company_id, 1], "company_id": target_company_id}

    # 3. Connect to Odoo via XML-RPC
    print(f"🔌 Connecting to Odoo models ({target_company_name})...")
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common")
    uid = common.authenticate(DB, USERNAME, API_KEY, {})
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")

    # Check for existing Sales Order in target company
    existing_sos = models.execute_kw(
        DB, uid, API_KEY,
        "sale.order", "search_read",
        [[["client_order_ref", "=", po_data["po_number"]], ["company_id", "=", target_company_id]]],
        {"fields": ["id", "name", "state", "amount_total", "company_id"], "order": "id desc", "context": odoo_context}
    )

    if existing_sos and not force_create:
        latest = existing_sos[0]
        print(f"\n⚠️  A Sales Order already exists in {target_company_name} for PO {po_data['po_number']}:")
        for so in existing_sos:
            print(f"   - {so['name']} (ID: {so['id']}) | State: {so['state']} | Total: Rp {so['amount_total']:,.0f}")
        
        lines = models.execute_kw(
            DB, uid, API_KEY,
            "sale.order.line", "search_read",
            [[["order_id", "=", latest["id"]]]],
            {"fields": ["name", "product_uom_qty", "price_unit", "price_subtotal"], "context": odoo_context}
        )
        print(f"\n📦 Verified line items in {latest['name']}:")
        for l in lines:
            print(f"   • {l['name']} | Qty: {l['product_uom_qty']:,.0f} @ Rp {l['price_unit']:,.0f} = Rp {l['price_subtotal']:,.0f}")

        print(f"\n👉 Using existing order in {target_company_name}: {latest['name']} (ID: {latest['id']})")
        return latest["id"]

    # 4. Find Customer & Warehouse (res.partner)
    clean_customer = re.sub(r"^(PT\.?|CV\.?)\s*", "", po_data["customer_name"], flags=re.IGNORECASE)
    clean_customer = re.sub(r"(CO\.,?\s*LTD\.?|LTD\.?|CORP\.?|INC\.?)$", "", clean_customer, flags=re.IGNORECASE).strip()
    
    partner_ids = models.execute_kw(
        DB, uid, API_KEY,
        "res.partner", "search",
        [[["name", "ilike", clean_customer]]],
        {"context": odoo_context}
    )

    if partner_ids:
        partner_id = partner_ids[0]
        print(f"Found customer ID: {partner_id}")
    else:
        partner_id = models.execute_kw(
            DB, uid, API_KEY,
            "res.partner", "create",
            [{"name": po_data["customer_name"], "customer_rank": 1, "company_id": target_company_id}],
            {"context": odoo_context}
        )
        print(f"Created customer ID: {partner_id}")

    # Check for warehouse child partner if specified
    partner_shipping_id = partner_id
    wh_raw = po_data.get("delivery_warehouse") or ""
    wh_match = re.search(r"(Cibitung|Sentul|Kosambi|DHL)", wh_raw, re.IGNORECASE)
    if wh_match:
        wh_search = wh_match.group(1)
        wh_partners = models.execute_kw(
            DB, uid, API_KEY,
            "res.partner", "search",
            [[["name", "ilike", wh_search]]],
            {"context": odoo_context}
        )
        if wh_partners:
            partner_shipping_id = wh_partners[0]

    # 5. Map Line Items & Products (strictly scoped to this client's products)
    odoo_products = models.execute_kw(
        DB, uid, API_KEY,
        "product.product", "search_read",
        [[["sale_ok", "=", True], ["name", "not ilike", "Pouch "], ["name", "not ilike", "Box "], ["name", "not ilike", "Sticker "]]],
        {"fields": ["id", "name", "list_price", "barcode"], "context": odoo_context}
    )

    order_lines = []
    print(f"\n📦 Mapping Products for Client [{client_tag}]:")
    for item in po_data["items"]:
        product_id = match_product(models, uid, item, odoo_products, client_tag=client_tag, odoo_context=odoo_context)
        if not product_id:
            raise ValueError(f"Product not found for client [{client_tag}]: [{item['kode_barang']}] {item['nama_barang']}")

        product_rec = models.execute_kw(
            DB, uid, API_KEY,
            "product.product", "read",
            [product_id], {"fields": ["name", "list_price"], "context": odoo_context}
        )[0]

        unit_price = item.get("unit_price") if item.get("unit_price") is not None else product_rec["list_price"]
        line_subtotal = item["quantity"] * unit_price
        order_lines.append((0, 0, {
            "product_id": product_id,
            "name": product_rec["name"],
            "product_uom_qty": item["quantity"],
            "price_unit": unit_price,
        }))
        print(f"  • {item['nama_barang']} -> {product_rec['name']} | Qty: {item['quantity']:,.0f} @ Rp {unit_price:,.0f} = Rp {line_subtotal:,.0f}")

    # 6. Create Sales Order in Target Company
    shipping_str = po_data.get("shipping_date") or ""
    notes_str = po_data.get("notes") or ""
    so_vals = {
        "company_id": target_company_id,
        "partner_id": partner_shipping_id,
        "partner_shipping_id": partner_shipping_id,
        "client_order_ref": po_data["po_number"],
        "note": f"Keterangan: {notes_str} | Tanggal Kirim: {shipping_str}",
        "order_line": order_lines,
    }

    sale_order_id = models.execute_kw(
        DB, uid, API_KEY,
        "sale.order", "create",
        [so_vals],
        {"context": odoo_context}
    )
    
    so_rec = models.execute_kw(
        DB, uid, API_KEY,
        "sale.order", "read",
        [sale_order_id], {"fields": ["name", "amount_total", "company_id"], "context": odoo_context}
    )[0]

    # Attach original PO file to Sales Order
    try:
        import base64
        with open(target_image, "rb") as f_att:
            b64_data = base64.b64encode(f_att.read()).decode("ascii")
        att_vals = {
            "name": os.path.basename(target_image),
            "datas": b64_data,
            "mimetype": mimetypes.guess_type(target_image)[0] or "application/octet-stream",
            "res_model": "sale.order",
            "res_id": sale_order_id,
        }
        models.execute_kw(DB, uid, API_KEY, "ir.attachment", "create", [att_vals], {"context": odoo_context})
        print(f"📎 Attached {os.path.basename(target_image)} to {so_rec['name']}")
    except Exception as e_att:
        print(f"⚠️ Note: could not attach file: {e_att}")

    print(f"\n🎉 SUCCESS! Sales Order created: {so_rec['name']} (ID: {sale_order_id}) in {so_rec['company_id'][1]}")
    print(f"   Total Amount: Rp {so_rec['amount_total']:,.0f}")
    return sale_order_id

if __name__ == "__main__":
    process_po_and_create_so()
