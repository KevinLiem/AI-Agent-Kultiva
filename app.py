import os
import sys
import re
import json
import mimetypes
import xmlrpc.client
import streamlit as st
import pandas as pd
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# Page Configuration
st.set_page_config(
    page_title="PO to Sales Order Agent",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #4B5563;
        margin-bottom: 1.5rem;
    }
    .status-card {
        padding: 1.2rem;
        border-radius: 0.6rem;
        background-color: #F3F4F6;
        margin-bottom: 1rem;
        border-left: 5px solid #2563EB;
    }
    .badge-alpha {
        background-color: #DBEAFE;
        color: #1E40AF;
        padding: 4px 10px;
        border-radius: 12px;
        font-weight: 600;
        display: inline-block;
    }
    .badge-kultiva {
        background-color: #D1FAE5;
        color: #065F46;
        padding: 4px 10px;
        border-radius: 12px;
        font-weight: 600;
        display: inline-block;
    }
</style>
""", unsafe_allow_html=True)

# --- CONFIGURATION DEFAULTS ---
# Automatically load local .env file if present
if os.path.exists(".env"):
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

def get_config(key, default=""):
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)

URL = get_config("ODOO_URL", "https://kultiva.odoo.com")
DB = get_config("ODOO_DB", "kultiva")
USERNAME = get_config("ODOO_USERNAME", "ppicjkt@kultivaco.com")
DEFAULT_ODOO_API_KEY = get_config("ODOO_API_KEY", "")
DEFAULT_GEMINI_API_KEY = get_config("GEMINI_API_KEY", "")

# --- SIDEBAR SETTINGS ---
with st.sidebar:
    st.image("https://img.icons8.com/color/96/purchase-order.png", width=64)
    st.title("⚙️ Settings")
    
    st.subheader("Odoo Credentials")
    st.caption(f"Server: `{URL}` | DB: `{DB}`")
    api_key = st.text_input("Odoo API Key", value=DEFAULT_ODOO_API_KEY, type="password")
    
    st.subheader("AI Configuration")
    gemini_key = st.text_input("Gemini API Key", value=DEFAULT_GEMINI_API_KEY, type="password")
    gemini_model = st.selectbox("Gemini Model", ["gemini-3.5-flash-lite", "gemini-2.5-flash"], index=0)
    
    st.divider()
    force_create = st.checkbox("Force create if SO already exists", value=False, help="Create a new SO even if a matching PO number exists in Odoo")
    
    st.caption("AI Agent v2.0 • Auto Multi-Company Routing")

# --- EXTRACTION SCHEMA ---
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

# --- COMPANY ROUTING HELPER ---
def get_client_and_company(customer_name, items=None):
    name_upper = (customer_name or "").upper()
    
    # If customer was accidentally parsed as internal company, check items
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
        words = re.findall(r"\b[A-Z]{3,}\b", name_upper)
        client_tag = None
        for w in words:
            if w not in ["THE", "LTD", "INC", "CORP", "FOODS", "FOOD", "INDONESIA", "MAKMUR", "PT", "CV", "ALPHA", "MAS", "INDO", "KULTIVA"]:
                client_tag = w
                break
        return 1, "PT Kultiva Indonesia Makmur", client_tag

# --- PRODUCT MATCHER ---
def match_product(models, uid, item, existing_products, client_tag=None, odoo_context=None):
    barcode = str(item.get("kode_barang", "")).strip()

    # 1. Check Odoo barcode field
    if barcode:
        product_ids = models.execute_kw(
            DB, uid, api_key,
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
        client_candidates = [
            p for p in existing_products
            if tag_upper in p["name"].upper() or f"[{tag_upper}" in p["name"].upper()
        ]
        if client_candidates:
            candidate_products = client_candidates
        else:
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

# --- MAIN UI ---
st.markdown('<div class="main-header">📦 Purchase Order to Sales Order Agent</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Upload a Purchase Order (PDF or Image) to extract items, match Odoo products, and create a Sales Order automatically.</div>', unsafe_allow_html=True)

uploaded_file = st.file_uploader(
    "Choose a Purchase Order file",
    type=["pdf", "jpeg", "jpg", "png"],
    help="Supports PDF, JPEG, JPG, and PNG purchase orders"
)

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    file_name = uploaded_file.name
    mime_type = uploaded_file.type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    col_preview, col_action = st.columns([1, 2])
    
    with col_preview:
        st.subheader("📄 Uploaded File")
        st.caption(f"**Name:** `{file_name}` | **Size:** `{len(file_bytes)/1024:.1f} KB`")
        if mime_type.startswith("image/"):
            st.image(file_bytes, use_container_width=True)
        elif mime_type == "application/pdf":
            st.info("📑 PDF Document loaded ready for processing.")
            
    with col_action:
        st.subheader("⚡ Process Order")
        if st.button("🚀 Analyze Document with AI & Match Odoo Products", type="primary", use_container_width=True):
            with st.spinner("🤖 Analyzing document with Gemini AI..."):
                try:
                    client = genai.Client(api_key=gemini_key)
                    response = client.models.generate_content(
                        model=gemini_model,
                        contents=[
                            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
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
                    st.session_state["po_data"] = po_data
                    st.success("✅ Extraction complete!")
                except Exception as e:
                    st.error(f"Error during AI extraction: {e}")

    # Display Extracted Info & Matching
    if "po_data" in st.session_state:
        po_data = st.session_state["po_data"]
        items_objs = [PurchaseOrderItem(**it) for it in po_data.get("items", [])]
        target_company_id, target_company_name, client_tag = get_client_and_company(po_data["customer_name"], items=items_objs)
        
        st.divider()
        st.subheader("📋 Order Details & Company Routing")
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("PO Number", po_data["po_number"])
        m2.metric("Customer", po_data["customer_name"])
        m3.metric("Order Date", po_data["po_date"])
        m4.metric("Shipping Date", po_data.get("shipping_date") or "-")
        
        badge_class = "badge-alpha" if target_company_id == 8 else "badge-kultiva"
        st.markdown(f"""
        <div style="margin-top: 10px; margin-bottom: 20px;">
            <strong>Target Odoo Company:</strong> <span class="{badge_class}">{target_company_name} (ID: {target_company_id})</span>
            &nbsp;|&nbsp; <strong>Brand Scope:</strong> <code>[{client_tag}]</code>
            &nbsp;|&nbsp; <strong>Warehouse:</strong> <code>{po_data.get('delivery_warehouse') or 'Main'}</code>
        </div>
        """, unsafe_allow_html=True)
        
        # Connect to Odoo and fetch products
        odoo_context = {"allowed_company_ids": [target_company_id, 1], "company_id": target_company_id}
        
        with st.spinner("Connecting to Odoo and matching products..."):
            try:
                common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common")
                uid = common.authenticate(DB, USERNAME, api_key, {})
                if not uid:
                    st.error("❌ Odoo authentication failed. Please verify your API key in the sidebar.")
                    st.stop()
                models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")
                
                # Check for existing Sales Order
                existing_sos = models.execute_kw(
                    DB, uid, api_key,
                    "sale.order", "search_read",
                    [[["client_order_ref", "=", po_data["po_number"]], ["company_id", "=", target_company_id]]],
                    {"fields": ["id", "name", "state", "amount_total", "company_id"], "order": "id desc", "context": odoo_context}
                )
                
                # Fetch candidate products
                odoo_products = models.execute_kw(
                    DB, uid, api_key,
                    "product.product", "search_read",
                    [[["sale_ok", "=", True], ["name", "not ilike", "Pouch "], ["name", "not ilike", "Box "], ["name", "not ilike", "Sticker "]]],
                    {"fields": ["id", "name", "list_price", "barcode"], "context": odoo_context}
                )
                
                # Match items
                table_rows = []
                unmatched = []
                order_lines_payload = []
                grand_total = 0.0
                
                for item in po_data["items"]:
                    pid = match_product(models, uid, item, odoo_products, client_tag=client_tag, odoo_context=odoo_context)
                    if pid:
                        rec = next(p for p in odoo_products if p["id"] == pid)
                        unit_price = item.get("unit_price") if item.get("unit_price") is not None else rec["list_price"]
                        subtotal = item["quantity"] * unit_price
                        grand_total += subtotal
                        table_rows.append({
                            "Status": "✅ Matched",
                            "PO Description": item["nama_barang"],
                            "Barcode": item.get("kode_barang") or "-",
                            "Matched Odoo Product": rec["name"],
                            "Qty": f"{item['quantity']:,.0f} {item.get('uom', 'PCS')}",
                            "Unit Price": f"Rp {unit_price:,.0f}",
                            "Subtotal": f"Rp {subtotal:,.0f}"
                        })
                        order_lines_payload.append((0, 0, {
                            "product_id": pid,
                            "name": rec["name"],
                            "product_uom_qty": item["quantity"],
                            "price_unit": unit_price,
                        }))
                    else:
                        table_rows.append({
                            "Status": "❌ Not Found",
                            "PO Description": item["nama_barang"],
                            "Barcode": item.get("kode_barang") or "-",
                            "Matched Odoo Product": "None",
                            "Qty": f"{item['quantity']:,.0f}",
                            "Unit Price": "-",
                            "Subtotal": "-"
                        })
                        unmatched.append(item["nama_barang"])
                
                st.subheader(f"📦 Product Line Items ({len(table_rows)} items)")
                df = pd.DataFrame(table_rows)
                st.dataframe(df, use_container_width=True, hide_index=True)
                
                c_tot1, c_tot2 = st.columns([3, 1])
                with c_tot2:
                    st.metric("Total Order Amount", f"Rp {grand_total:,.0f}")
                
                if unmatched:
                    st.warning(f"⚠️ {len(unmatched)} product(s) could not be matched automatically. Please review above.")
                
                st.divider()
                
                # Check Existing Order Warning
                if existing_sos and not force_create:
                    latest = existing_sos[0]
                    st.warning(f"⚠️ A Sales Order already exists in **{target_company_name}** for PO **{po_data['po_number']}**: **{latest['name']}** (Total: Rp {latest['amount_total']:,.0f})")
                    st.info("💡 If you want to create a new order anyway, check 'Force create if SO already exists' in the sidebar.")
                else:
                    if not unmatched:
                        if st.button(f"✨ Create Sales Order in {target_company_name}", type="primary", use_container_width=True):
                            with st.spinner("Creating Sales Order in Odoo..."):
                                # 1. Find Customer Partner
                                clean_customer = re.sub(r"^(PT\.?|CV\.?)\s*", "", po_data["customer_name"], flags=re.IGNORECASE)
                                clean_customer = re.sub(r"(CO\.,?\s*LTD\.?|LTD\.?|CORP\.?|INC\.?)$", "", clean_customer, flags=re.IGNORECASE).strip()
                                
                                partner_ids = models.execute_kw(
                                    DB, uid, api_key,
                                    "res.partner", "search",
                                    [[["name", "ilike", clean_customer]]],
                                    {"context": odoo_context}
                                )
                                if partner_ids:
                                    partner_id = partner_ids[0]
                                else:
                                    partner_id = models.execute_kw(
                                        DB, uid, api_key,
                                        "res.partner", "create",
                                        [{"name": po_data["customer_name"], "customer_rank": 1, "company_id": target_company_id}],
                                        {"context": odoo_context}
                                    )
                                
                                # 2. Check Warehouse Partner
                                partner_shipping_id = partner_id
                                wh_raw = po_data.get("delivery_warehouse") or ""
                                wh_match = re.search(r"(Cibitung|Sentul|Kosambi)", wh_raw, re.IGNORECASE)
                                if wh_match:
                                    wh_search = wh_match.group(1)
                                    wh_partners = models.execute_kw(
                                        DB, uid, api_key,
                                        "res.partner", "search",
                                        [[["parent_id", "=", partner_id], ["name", "ilike", wh_search]]],
                                        {"context": odoo_context}
                                    )
                                    if wh_partners:
                                        partner_shipping_id = wh_partners[0]
                                
                                # 3. Create SO
                                shipping_str = po_data.get("shipping_date") or ""
                                notes_str = po_data.get("notes") or ""
                                so_vals = {
                                    "company_id": target_company_id,
                                    "partner_id": partner_shipping_id,
                                    "partner_shipping_id": partner_shipping_id,
                                    "client_order_ref": po_data["po_number"],
                                    "note": f"Keterangan: {notes_str} | Tanggal Kirim: {shipping_str}",
                                    "order_line": order_lines_payload,
                                }
                                
                                sale_order_id = models.execute_kw(
                                    DB, uid, api_key,
                                    "sale.order", "create",
                                    [so_vals],
                                    {"context": odoo_context}
                                )
                                
                                so_rec = models.execute_kw(
                                    DB, uid, api_key,
                                    "sale.order", "read",
                                    [sale_order_id], {"fields": ["name", "amount_total", "company_id"], "context": odoo_context}
                                )[0]
                                
                                st.balloons()
                                st.success(f"🎉 **Sales Order Created Successfully!**")
                                st.markdown(f"""
                                - **Order Number:** `{so_rec['name']}` (ID: `{sale_order_id}`)
                                - **Company:** `{so_rec['company_id'][1]}`
                                - **Total Amount:** **Rp {so_rec['amount_total']:,.0f}**
                                """)
                    else:
                        st.error("Cannot create Sales Order until all products are matched.")
            except Exception as e:
                st.error(f"Error connecting to Odoo: {e}")

