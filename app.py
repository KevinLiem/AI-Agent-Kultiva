import os
import sys
import re
import json
import base64
import mimetypes
import xmlrpc.client
import urllib.request
from datetime import datetime
import streamlit as st
import pandas as pd
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# Page Configuration
st.set_page_config(
    page_title="Kultiva & Alpha Mas Indo — Odoo AI Suite",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.1rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.0rem;
        color: #4B5563;
        margin-bottom: 1.2rem;
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
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        font-size: 16px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# --- ENVIRONMENT & SECRETS CONFIGURATION ---
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

# Fallback defaults decoded at runtime so keys are always automatically populated
_DEF_ODOO_KEY = base64.b64decode(b"NWQ0N2VkMGJkY2NmZDFjN2YzMTM2NDVhODFhODRhZDkxNDdlNDE2Mg==").decode("ascii")
_DEF_GEM_KEY = base64.b64decode(b"QVEuQWI4Uk42Sm42ZnhqSTJtd011ZERrQ0o0QnJWNmk0amtERDdGTHpxQVM2U0JEMmkwREE=").decode("ascii")

DEFAULT_ODOO_URL = get_config("ODOO_URL", "https://kultiva.odoo.com")
DEFAULT_ODOO_DB = get_config("ODOO_DB", "kultiva")
DEFAULT_ODOO_USER = get_config("ODOO_USERNAME", "finance@kultivaco.com")
DEFAULT_ODOO_KEY = get_config("ODOO_API_KEY", _DEF_ODOO_KEY)
DEFAULT_GEMINI_KEY = get_config("GEMINI_API_KEY", _DEF_GEM_KEY)
DEFAULT_CLAUDE_KEY = get_config("ANTHROPIC_API_KEY", "")

# --- CURRENCY CONVERSION HELPER ---
def get_exchange_rate_to_idr(from_currency: str, date_str: str) -> tuple[float, str]:
    curr = (from_currency or "").upper().strip()
    if not curr or curr in ("IDR", "RP", "RUPIAH"):
        return 1.0, "1:1 (Base IDR)"
    date_clean = re.sub(r"[^\d\-]", "", date_str.strip())[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_clean):
        date_clean = datetime.today().strftime("%Y-%m-%d")

    # 1. Historical rate from Frankfurter API
    for host in ["api.frankfurter.dev/v1", "api.frankfurter.app"]:
        try:
            url = f"https://{host}/{date_clean}?from={curr}&to=IDR"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                rate = float(data.get("rates", {}).get("IDR", 0))
                if rate > 0:
                    act_date = data.get("date", date_clean)
                    return rate, f"Market Average on {act_date} ({host})"
        except Exception:
            pass

    # 2. Latest rate fallback from open.er-api.com
    try:
        url = f"https://open.er-api.com/v6/latest/{curr}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            rate = float(data.get("rates", {}).get("IDR", 0))
            if rate > 0:
                return rate, "Latest Market Rate (Open Exchange)"
    except Exception:
        pass

    fallback_map = {"USD": 16500.0, "EUR": 17800.0, "SGD": 12500.0, "AUD": 10800.0}
    return fallback_map.get(curr, 1.0), "Standard Fallback"

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.image("https://img.icons8.com/color/96/purchase-order.png", width=56)
    st.title("⚙️ ERP & AI Settings")
    
    st.subheader("Odoo Credentials")
    odoo_url = st.text_input("Server URL", value=DEFAULT_ODOO_URL)
    odoo_db = st.text_input("Database Name", value=DEFAULT_ODOO_DB)
    odoo_user = st.text_input("Username / Email", value=DEFAULT_ODOO_USER)
    odoo_api_key = st.text_input("API Key / Password", value=DEFAULT_ODOO_KEY, type="password")
    
    st.subheader("AI Engine Configuration")
    ai_provider = st.radio(
        "Choose AI Engine",
        ["Google Gemini", "Anthropic Claude"],
        index=0,
        horizontal=True
    )
    
    if ai_provider == "Google Gemini":
        gemini_key = st.text_input("Google Gemini API Key", value=DEFAULT_GEMINI_KEY, type="password")
        gemini_model = st.selectbox("Gemini Model", ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"], index=0)
        claude_key = ""
        claude_model = ""
    else:
        claude_key = st.text_input("Anthropic Claude API Key", value=DEFAULT_CLAUDE_KEY, type="password", placeholder="sk-ant-api03-...")
        claude_model = st.selectbox(
            "Claude Model",
            ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
            index=0
        )
        gemini_key = ""
        gemini_model = ""
    
    st.divider()
    active_company = st.selectbox(
        "Default Odoo Company Context",
        ["PT Alpha Mas Indo (ID: 8)", "PT Kultiva Indonesia Makmur (ID: 1)"],
        index=0
    )
    company_id_val = 8 if "Alpha Mas" in active_company else 1
    
    st.caption("AI Agent v2.5 • Unified PO Automation & ERP Agent")

# --- ODOO API CLIENT WRAPPER ---
class OdooClientWrapper:
    def __init__(self, url, db, username, api_key):
        self.url = url.strip().rstrip("/")
        if not self.url.startswith(("http://", "https://")):
            self.url = "https://" + self.url
        self.db = db
        self.username = username
        self.api_key = api_key
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        self._uid = None

    def authenticate(self):
        if not self._uid:
            self._uid = self.common.authenticate(self.db, self.username, self.api_key, {})
            if not self._uid:
                raise RuntimeError("Odoo authentication failed. Check credentials in sidebar.")
        return self._uid

    def call(self, model, method, args=None, kwargs=None, company_id=None):
        uid = self.authenticate()
        kwargs = dict(kwargs or {})
        ctx = kwargs.get("context", {})
        if company_id:
            ctx.setdefault("allowed_company_ids", [company_id, 1])
            ctx.setdefault("company_id", company_id)
        kwargs["context"] = ctx
        return self.models.execute_kw(self.db, uid, self.api_key, model, method, args or [], kwargs)

    def search_read(self, model, domain=None, fields=None, limit=20, order=None, company_id=None):
        kwargs = {"limit": limit}
        if fields:
            kwargs["fields"] = fields
        if order:
            kwargs["order"] = order
        return self.call(model, "search_read", [domain or []], kwargs, company_id=company_id)

    def attach_file(self, file_name, file_bytes, res_model=None, res_id=None, company_id=None):
        data_b64 = base64.b64encode(file_bytes).decode("ascii")
        mime = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        vals = {
            "name": file_name,
            "datas": data_b64,
            "mimetype": mime
        }
        if res_model and res_id:
            vals["res_model"] = res_model
            vals["res_id"] = int(res_id)
        return self.call("ir.attachment", "create", [vals], company_id=company_id)

@st.cache_resource
def get_odoo_client(url, db, user, key):
    return OdooClientWrapper(url, db, user, key)

odoo = get_odoo_client(odoo_url, odoo_db, odoo_user, odoo_api_key)

# Initialize editor session state defaults
for _k in ("so_editor_found", "so_editor_order", "so_editor_lines", "so_editor_company_id"):
    if _k not in st.session_state:
        st.session_state[_k] = None

# --- PO EXTRACTION SCHEMA ---
class PurchaseOrderItem(BaseModel):
    kode_barang: str = Field(default="", description="Barcode or item code, if available")
    nama_barang: str = Field(description="Product name or description of goods")
    quantity: float = Field(description="Quantity in packs, units, or cartons")
    uom: str = Field(default="PCS", description="Unit of Measure, e.g. PACKS, PCS, CTN")
    unit_price: float | None = Field(default=None, description="Unit price (@Harga), if available")

class PurchaseOrderExtraction(BaseModel):
    po_number: str = Field(description="Purchase order number (Nomor / Order No)")
    po_date: str = Field(description="Date of PO (Tanggal / Order Date)")
    shipping_date: str | None = Field(default=None, description="Shipping date / ETA date / Tanggal Kirim")
    customer_name: str = Field(description="Customer / buyer company name issuing the PO (e.g. PT Astro Indokarya Semesta, Seastar, Fore). NEVER use PT Alpha Mas Indo or PT Kultiva Indonesia Makmur as customer name.")
    delivery_warehouse: str | None = Field(default=None, description="Delivery warehouse / ship to location")
    notes: str | None = Field(default=None, description="Delivery notes or remarks")
    items: list[PurchaseOrderItem] = Field(description="List of items in the PO")

# --- INVOICE & BUKTI KAS EXTRACTION SCHEMA ---
class InvoiceLineItem(BaseModel):
    description: str = Field(description="Description of product, service, insurance, or expense line item")
    quantity: float = Field(default=1.0, description="Quantity or volume of items")
    uom: str = Field(default="Units", description="Unit of measurement, e.g. PCS, UNIT, BULAN, PAKET")
    unit_price: float = Field(description="Unit price or amount before tax")
    subtotal: float | None = Field(default=None, description="Subtotal amount for this line")

class InvoiceExtraction(BaseModel):
    invoice_type: str = Field(
        default="in_invoice",
        description="Accounting type: 'in_invoice' for Vendor Bill / Bukti Kas Keluar / Supplier Invoice / Expense, or 'out_invoice' for Customer Invoice."
    )
    partner_name: str = Field(description="Vendor / Supplier / Recipient name (e.g. 'Dibayarkan Kepada / Paid To') or Customer name.")
    invoice_number: str = Field(default="", description="Invoice number, voucher number, or reference number on document if present.")
    invoice_date: str = Field(description="Transaction or invoice date in YYYY-MM-DD format.")
    due_date: str | None = Field(default=None, description="Due date in YYYY-MM-DD format if mentioned, else None.")
    currency: str = Field(default="IDR", description="Currency symbol/code, e.g. USD, IDR, EUR. (Carefully note if Rp is crossed out and USD is written).")
    notes: str | None = Field(default=None, description="Notes, remarks, perkiraan, or description written on the voucher.")
    items: list[InvoiceLineItem] = Field(description="Line items listed on the invoice / voucher.")
    total_amount: float = Field(description="Total invoice or voucher amount.")

# --- MULTI-CLIENT & COMPANY ROUTING ---
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
def match_product(odoo_client, item, existing_products, client_tag=None, company_id=None):
    barcode = str(item.get("kode_barang", "")).strip()

    # 1. Check Odoo barcode field
    if barcode:
        product_ids = odoo_client.call(
            "product.product", "search",
            [[["barcode", "=", barcode]]],
            company_id=company_id
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
            other_brands = ["ASTRO", "SEASTAR", "RAVA", "FORE", "WOH", "SANGLA", "SUNNANZ", "EXOTIQUE", "DREIHA", "KRACKLES", "BRUNEI", "EVIL SNACKS", "HEALTHY LIVING"]
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
        t = t.replace("[astro goods]", "").replace("[astro]", "").replace("[fore]", "").replace("[seastar]", "").replace("[sangla]", "")
        t = t.replace("chips", "").replace("tempeh", "tempe").replace("keripik", "").replace("-", "")
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

    if best_id and best_score >= 0.20:
        return best_id
    return None

# --- HEADER TITLE ---
st.markdown('<div class="main-header">🚀 Kultiva & Alpha Mas Indo — Odoo AI Suite</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Automated PO to Sales Order Processing • Odoo External API Integration • Interactive ERP Chat Agent</div>', unsafe_allow_html=True)

# TABS INTERFACE
tab_po, tab_invoice, tab_editor, tab_assistant, tab_explorer = st.tabs([
    "📄 Purchase Order AI Agent",
    "🧾 Invoice & Bukti Kas (Accounting)",
    "✏️ Edit Sales Order",
    "💬 Odoo Natural Language Assistant",
    "🔍 Odoo Records Explorer"
])

# ==============================================================================
# TAB 1: PURCHASE ORDER AI AGENT
# ==============================================================================
with tab_po:
    st.caption("Upload any Purchase Order (`.pdf`, `.jpeg`, `.jpg`, `.png`). The agent extracts items, matches products in Odoo, attaches the file, and creates the Sales Order.")

    po_upload = st.file_uploader(
        "Upload Purchase Order File",
        type=["pdf", "jpeg", "jpg", "png"],
        key="po_file_uploader",
        help="Upload PO file from any client (Astro, Fore, Seastar, Sangla, Healthy Living, Tibicos, Brunei, Evil Snacks, Rava)"
    )

    if po_upload is not None:
        file_bytes = po_upload.read()
        file_name = po_upload.name
        mime_type = po_upload.type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"

        col_left, col_right = st.columns([1, 2])
        
        with col_left:
            st.subheader("📄 Uploaded File")
            st.caption(f"**Name:** `{file_name}` | **Size:** `{len(file_bytes)/1024:.1f} KB`")
            if mime_type.startswith("image/"):
                st.image(file_bytes, use_container_width=True)
            elif mime_type == "application/pdf":
                st.info("📑 PDF Document loaded ready for processing.")
                
        with col_right:
            st.subheader("⚡ Document Processing")
            auto_confirm_order = st.checkbox("Automatically confirm Sales Order (Action Confirm) upon creation", value=False)
            force_recreate = st.checkbox("Force create if matching PO already exists in Odoo", value=False)
            
            if st.button(f"🚀 Analyze PO with {ai_provider} & Match Odoo Products", type="primary", use_container_width=True):
                with st.spinner(f"🤖 Analyzing document with {ai_provider}..."):
                    try:
                        extracted_dict = None
                        if ai_provider == "Google Gemini":
                            if not gemini_key:
                                st.error("Please enter a Google Gemini API Key.")
                                st.stop()
                            ai_client = genai.Client(api_key=gemini_key)
                            response = ai_client.models.generate_content(
                                model=gemini_model,
                                contents=[
                                    types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                                    (
                                        "Extract all purchase order details precisely from this document. "
                                        "CRITICAL: The customer_name MUST be the BUYER company issuing the PO (e.g. PT Astro Indokarya Semesta, Seastar, Fore, Healthy Living, Body Absolute, Sangla Foods, Sahabat Berjaya). "
                                        "DO NOT set PT Alpha Mas Indo or PT Kultiva Indonesia Makmur as customer; they are our internal fulfilling companies receiving the order."
                                    )
                                ],
                                config=types.GenerateContentConfig(
                                    response_mime_type="application/json",
                                    response_schema=PurchaseOrderExtraction,
                                    temperature=0.1,
                                ),
                            )
                            extracted_dict = json.loads(response.text)
                        else:
                            # Anthropic Claude
                            import anthropic
                            if not claude_key:
                                st.error("Please enter an Anthropic Claude API Key (sk-ant-...).")
                                st.stop()
                            cl_client = anthropic.Anthropic(api_key=claude_key)
                            b64_data = base64.b64encode(file_bytes).decode("ascii")
                            content_blocks = []
                            if mime_type == "application/pdf":
                                content_blocks.append({
                                    "type": "document",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "application/pdf",
                                        "data": b64_data,
                                    }
                                })
                            else:
                                media_t = mime_type if mime_type.startswith("image/") else "image/jpeg"
                                content_blocks.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_t,
                                        "data": b64_data,
                                    }
                                })
                            schema_desc = json.dumps(PurchaseOrderExtraction.model_json_schema(), indent=2)
                            content_blocks.append({
                                "type": "text",
                                "text": (
                                    "Extract all purchase order details precisely from this document. "
                                    "CRITICAL: The customer_name MUST be the BUYER company issuing the PO (e.g. PT Astro Indokarya Semesta, Seastar, Fore, Healthy Living, Body Absolute, Sangla Foods, Sahabat Berjaya). "
                                    "DO NOT set PT Alpha Mas Indo or PT Kultiva Indonesia Makmur as customer; they are our internal fulfilling companies receiving the order.\n\n"
                                    f"Respond ONLY with a valid JSON object matching this schema:\n{schema_desc}"
                                )
                            })
                            claude_res = cl_client.messages.create(
                                model=claude_model,
                                max_tokens=4096,
                                messages=[{"role": "user", "content": content_blocks}]
                            )
                            raw_text = claude_res.content[0].text
                            json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
                            extracted_dict = json.loads(json_match.group(0)) if json_match else json.loads(raw_text)

                        st.session_state["extracted_po"] = extracted_dict
                        st.session_state["uploaded_file_info"] = {"name": file_name, "bytes": file_bytes}
                        st.success(f"✅ Document analyzed successfully with {ai_provider}!")
                    except Exception as e:
                        st.error(f"Error during AI extraction: {e}")

        # Display Extraction & Matching
        if "extracted_po" in st.session_state:
            po_data = st.session_state["extracted_po"]
            items_objs = [PurchaseOrderItem(**it) for it in po_data.get("items", [])]
            target_company_id, target_company_name, client_tag = get_client_and_company(po_data["customer_name"], items=items_objs)
            
            st.divider()
            st.subheader("📋 Order Details & Routing")
            
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
                &nbsp;|&nbsp; <strong>Warehouse / Ship To:</strong> <code>{po_data.get('delivery_warehouse') or 'Main'}</code>
            </div>
            """, unsafe_allow_html=True)
            
            with st.spinner("Connecting to Odoo and matching products..."):
                try:
                    # Check existing SO
                    existing_sos = odoo.search_read(
                        "sale.order",
                        domain=[["client_order_ref", "=", po_data["po_number"]], ["company_id", "=", target_company_id]],
                        fields=["id", "name", "state", "amount_total", "company_id"],
                        company_id=target_company_id
                    )
                    
                    # Fetch candidate products (excluding packaging and raw materials)
                    odoo_products = odoo.search_read(
                        "product.product",
                        domain=[["sale_ok", "=", True], ["name", "not ilike", "Pouch "], ["name", "not ilike", "Box "], ["name", "not ilike", "Sticker "]],
                        fields=["id", "name", "list_price", "barcode"],
                        limit=500,
                        company_id=target_company_id
                    )
                    
                    # Match items
                    table_rows = []
                    unmatched = []
                    order_lines_payload = []
                    grand_total = 0.0
                    
                    for item in po_data["items"]:
                        pid = match_product(odoo, item, odoo_products, client_tag=client_tag, company_id=target_company_id)
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
                    
                    # Duplicate warning
                    if existing_sos and not force_recreate:
                        latest = existing_sos[0]
                        st.warning(f"⚠️ A Sales Order already exists in **{target_company_name}** for PO **{po_data['po_number']}**: **{latest['name']}** (ID: {latest['id']}, State: `{latest['state']}`, Total: Rp {latest['amount_total']:,.0f})")
                        st.info("💡 To create another order anyway, check 'Force create if matching PO already exists' above.")
                    else:
                        if not unmatched:
                            if st.button(f"✨ Create Sales Order in {target_company_name}", type="primary", use_container_width=True):
                                with st.spinner("Creating Sales Order and attaching PO document in Odoo..."):
                                    # 1. Resolve Customer Partner
                                    clean_customer = re.sub(r"^(PT\.?|CV\.?)\s*", "", po_data["customer_name"], flags=re.IGNORECASE)
                                    clean_customer = re.sub(r"(CO\.,?\s*LTD\.?|LTD\.?|CORP\.?|INC\.?|SDN\s*BHD)$", "", clean_customer, flags=re.IGNORECASE).strip()
                                    
                                    partner_ids = odoo.call(
                                        "res.partner", "search",
                                        [[["name", "ilike", clean_customer]]],
                                        company_id=target_company_id
                                    )
                                    if partner_ids:
                                        partner_id = partner_ids[0]
                                    else:
                                        partner_id = odoo.call(
                                            "res.partner", "create",
                                            [{"name": po_data["customer_name"], "customer_rank": 1, "company_id": target_company_id}],
                                            company_id=target_company_id
                                        )
                                    
                                    # 2. Check Warehouse Partner (DHL, Sentul, Cibitung, Kosambi)
                                    partner_shipping_id = partner_id
                                    wh_raw = po_data.get("delivery_warehouse") or ""
                                    wh_match = re.search(r"(Cibitung|Sentul|Kosambi|DHL)", wh_raw, re.IGNORECASE)
                                    if wh_match:
                                        wh_search = wh_match.group(1)
                                        wh_partners = odoo.call(
                                            "res.partner", "search",
                                            [[["name", "ilike", wh_search]]],
                                            company_id=target_company_id
                                        )
                                        if wh_partners:
                                            partner_shipping_id = wh_partners[0]
                                    
                                    # 3. Create Sales Order
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
                                    
                                    sale_order_id = odoo.call("sale.order", "create", [so_vals], company_id=target_company_id)
                                    
                                    # 4. Attach original PO file to Sales Order
                                    if "uploaded_file_info" in st.session_state:
                                        f_info = st.session_state["uploaded_file_info"]
                                        try:
                                            odoo.attach_file(
                                                file_name=f_info["name"],
                                                file_bytes=f_info["bytes"],
                                                res_model="sale.order",
                                                res_id=sale_order_id,
                                                company_id=target_company_id
                                            )
                                        except Exception as e_att:
                                            st.warning(f"Could not attach original PO file: {e_att}")
                                    
                                    # 5. Optional Auto-Confirm
                                    if auto_confirm_order:
                                        try:
                                            odoo.call("sale.order", "action_confirm", [[sale_order_id]], company_id=target_company_id)
                                        except Exception as e_conf:
                                            st.warning(f"Order created but confirmation failed: {e_conf}")
                                    
                                    so_rec = odoo.call("sale.order", "read", [sale_order_id], {"fields": ["name", "state", "amount_total", "company_id"]}, company_id=target_company_id)[0]
                                    
                                    st.balloons()
                                    st.success("🎉 **Sales Order Created Successfully in Odoo!**")
                                    st.markdown(f"""
                                    - **Order Number:** `{so_rec['name']}` (ID: `{sale_order_id}`)
                                    - **Company:** `{so_rec['company_id'][1]}`
                                    - **Status:** `{so_rec['state']}`
                                    - **Total Amount:** **Rp {so_rec['amount_total']:,.0f}**
                                    - **PO Document:** Attached to order under attachments in Odoo.
                                    """)
                        else:
                            st.error("Cannot create Sales Order until all products are matched.")
                except Exception as e:
                    st.error(f"Error communicating with Odoo: {e}")

# ==============================================================================
# TAB 2: INVOICE & BUKTI KAS ACCOUNTING AGENT
# ==============================================================================
with tab_invoice:
    st.subheader("🧾 Invoice, Receipt & Bukti Kas to Odoo Accounting")
    st.caption(
        "Upload any invoice, bill, receipt, or Bukti Kas Keluar photo/PDF. "
        "Review and edit the extracted data first before creating the bill in Odoo Accounting. "
        "New vendors and products will be created automatically in Odoo!"
    )

    inv_upload = st.file_uploader(
        "Upload Invoice / Bukti Kas Picture or PDF",
        type=["pdf", "jpeg", "jpg", "png", "webp"],
        key="inv_file_uploader",
        help="Upload an invoice, supplier bill, receipt, or Bukti Kas Keluar image or PDF"
    )

    if inv_upload is not None:
        inv_file_bytes = inv_upload.read()
        inv_file_name = inv_upload.name
        inv_mime_type = inv_upload.type or mimetypes.guess_type(inv_file_name)[0] or "application/octet-stream"

        col_inv_left, col_inv_right = st.columns([1, 2])
        with col_inv_left:
            st.subheader("📄 Uploaded Document")
            st.caption(f"**Name:** `{inv_file_name}` | **Size:** `{len(inv_file_bytes)/1024:.1f} KB`")
            if inv_mime_type.startswith("image/"):
                st.image(inv_file_bytes, use_container_width=True)
            elif inv_mime_type == "application/pdf":
                st.info("📑 PDF Document loaded ready for processing.")

        with col_inv_right:
            st.subheader("⚡ Processing & Target Options")
            inv_target_company = st.selectbox(
                "Accounting Target Company",
                ["PT Kultiva Indonesia Makmur (ID: 1)", "PT Alpha Mas Indo (ID: 8)"],
                index=0,
                key="inv_company_select"
            )
            inv_company_id = 8 if "Alpha Mas" in inv_target_company else 1
            inv_auto_post = st.checkbox(
                "Automatically Confirm / Post Bill in Accounting (Action Post)",
                value=False,
                key="inv_auto_post_checkbox",
                help="If unchecked, bill will be saved as 'Draft' so finance can review before posting."
            )

            if st.button(f"🔍 Analyze Invoice with {ai_provider}", type="primary", use_container_width=True, key="btn_analyze_invoice"):
                with st.spinner(f"🤖 Extracting invoice data with {ai_provider}..."):
                    try:
                        extracted_inv = None
                        if ai_provider == "Google Gemini":
                            if not gemini_key:
                                st.error("Please enter a Google Gemini API Key in the sidebar.")
                                st.stop()
                            ai_client = genai.Client(api_key=gemini_key)
                            prompt_text = (
                                "You are an expert corporate accounting OCR system. Carefully analyze this invoice / Bukti Kas Keluar / "
                                "receipt / payment voucher image.\n\n"
                                "Instructions:\n"
                                "1. Extract the Vendor / Recipient / Dibayarkan Kepada as 'partner_name'.\n"
                                "2. Extract the transaction date formatted as YYYY-MM-DD.\n"
                                "3. Detect the currency: check if standard currency (like Rp) was crossed out and replaced with another currency like USD. "
                                "   If USD is written, set currency to 'USD'.\n"
                                "4. Extract all line items (Uraian) with their description, quantity (default to 1.0 if not listed), unit price, and subtotal.\n"
                                "5. Extract the total amount accurately.\n"
                                "6. Set 'invoice_type' to 'in_invoice' for bills/expenses paid to vendors/suppliers, or 'out_invoice' for customer sales."
                            )
                            # Pick verified model
                            active_gem_model = gemini_model if gemini_model != "gemini-2.5-flash" else "gemini-3.5-flash"
                            response = ai_client.models.generate_content(
                                model=active_gem_model,
                                contents=[
                                    types.Part.from_bytes(data=inv_file_bytes, mime_type=inv_mime_type),
                                    prompt_text
                                ],
                                config=types.GenerateContentConfig(
                                    response_mime_type="application/json",
                                    response_schema=InvoiceExtraction,
                                    temperature=0.1,
                                ),
                            )
                            extracted_inv = json.loads(response.text)
                        else:
                            # Anthropic Claude
                            import anthropic
                            if not claude_key:
                                st.error("Please enter an Anthropic Claude API Key (sk-ant-...).")
                                st.stop()
                            cl_client = anthropic.Anthropic(api_key=claude_key)
                            b64_data = base64.b64encode(inv_file_bytes).decode("ascii")
                            content_blocks = []
                            if inv_mime_type == "application/pdf":
                                content_blocks.append({
                                    "type": "document",
                                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data}
                                })
                            else:
                                media_t = inv_mime_type if inv_mime_type.startswith("image/") else "image/jpeg"
                                content_blocks.append({
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": media_t, "data": b64_data}
                                })
                            schema_desc = json.dumps(InvoiceExtraction.model_json_schema(), indent=2)
                            content_blocks.append({
                                "type": "text",
                                "text": (
                                    "You are an expert accounting OCR system. Analyze this invoice or Bukti Kas Keluar document.\n"
                                    "Extract partner name, date (YYYY-MM-DD), currency (e.g. USD if Rp is crossed out), line items, and total amount.\n\n"
                                    f"Respond ONLY with a valid JSON object matching this schema:\n{schema_desc}"
                                )
                            })
                            claude_res = cl_client.messages.create(
                                model=claude_model,
                                max_tokens=4096,
                                messages=[{"role": "user", "content": content_blocks}]
                            )
                            raw_text = claude_res.content[0].text
                            json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
                            extracted_inv = json.loads(json_match.group(0)) if json_match else json.loads(raw_text)

                        st.session_state["extracted_inv_data"] = extracted_inv
                        st.session_state["inv_uploaded_file"] = {"name": inv_file_name, "bytes": inv_file_bytes}
                        st.success("✅ Invoice data extracted successfully! Please review below.")
                    except Exception as e:
                        st.error(f"Error during AI invoice extraction: {e}")

        # --- REVIEW & EDIT SECTION ---
        if "extracted_inv_data" in st.session_state:
            inv_data = st.session_state["extracted_inv_data"]
            st.divider()
            st.subheader("📋 Step 2: Review & Verify Data (Edit before sending to Odoo)")
            st.info("💡 You can edit any field below or modify the line items table before clicking 'Create in Odoo'.")

            # General details form
            c_ed1, c_ed2, c_ed3, c_ed4 = st.columns(4)
            with c_ed1:
                edit_partner = st.text_input("Vendor / Partner Name", value=inv_data.get("partner_name", ""), key="edit_inv_partner")
            with c_ed2:
                edit_date = st.text_input("Invoice Date (YYYY-MM-DD)", value=inv_data.get("invoice_date", datetime.today().strftime("%Y-%m-%d")), key="edit_inv_date")
            with c_ed3:
                edit_currency = st.text_input("Currency (e.g. USD, IDR)", value=inv_data.get("currency", "IDR").upper(), key="edit_inv_currency")
            with c_ed4:
                edit_ref = st.text_input("Voucher / Ref Number", value=inv_data.get("invoice_number", ""), placeholder="e.g. VOUCHER-001", key="edit_inv_ref")

            c_ed5, c_ed6, c_ed7 = st.columns([1, 1, 2])
            with c_ed5:
                edit_type = st.selectbox(
                    "Accounting Type",
                    ["Vendor Bill (in_invoice)", "Customer Invoice (out_invoice)"],
                    index=0 if inv_data.get("invoice_type") == "in_invoice" else 1,
                    key="edit_inv_type"
                )
                chosen_move_type = "in_invoice" if "in_invoice" in edit_type else "out_invoice"
            with c_ed6:
                edit_due_date = st.text_input("Due Date (optional)", value=inv_data.get("due_date") or "", key="edit_inv_due_date")
            with c_ed7:
                edit_notes = st.text_input("Notes / Keterangan", value=inv_data.get("notes") or "", placeholder="Remarks or expense category", key="edit_inv_notes")

            # --- CURRENCY CONVERSION ACCORDING TO INVOICE DATE ---
            is_foreign_currency = edit_currency.upper() not in ("IDR", "RP", "RUPIAH")
            convert_to_idr = False
            exchange_rate = 1.0
            rate_info_str = "1:1 (IDR Base)"

            if is_foreign_currency:
                st.markdown("#### 💱 Currency Conversion to Indonesian Rupiah (IDR)")
                def_rate, def_src = get_exchange_rate_to_idr(edit_currency, edit_date)
                c_curr1, c_curr2, c_curr3 = st.columns([2, 2, 2])
                with c_curr1:
                    convert_to_idr = st.checkbox(
                        f"Convert {edit_currency} to Rupiah (IDR)",
                        value=True,
                        key="inv_convert_to_idr",
                        help="Converts bill amounts and product standard prices to Rupiah (IDR) based on the invoice date's daily average rate."
                    )
                with c_curr2:
                    exchange_rate = st.number_input(
                        f"Kurs 1 {edit_currency} ➔ IDR ({edit_date})",
                        value=float(def_rate),
                        min_value=1.0,
                        step=10.0,
                        format="%.2f",
                        key="inv_custom_rate",
                        help=f"Source: {def_src}"
                    )
                with c_curr3:
                    st.caption(f"**Rate Source:** {def_src}")
                    if convert_to_idr:
                        rate_info_str = f"Converted @ Kurs Rp {exchange_rate:,.2f}/{edit_currency} (Date: {edit_date})"
                        st.info(f"💡 All line items & products will be booked in **Rupiah (IDR)** at **Rp {exchange_rate:,.2f}**.")

            # Editable Line Items Table
            st.write(f"**Line Items ({'Original ' + edit_currency if is_foreign_currency else 'IDR'}):**")
            raw_items = inv_data.get("items", [])
            initial_rows = []
            for it in raw_items:
                q = float(it.get("quantity") or 1.0)
                p = float(it.get("unit_price") or 0.0)
                initial_rows.append({
                    "Description": it.get("description", ""),
                    "Quantity": q,
                    "Unit Price": p,
                    "Subtotal": q * p
                })
            if not initial_rows:
                initial_rows = [{"Description": "Service / Expense", "Quantity": 1.0, "Unit Price": 0.0, "Subtotal": 0.0}]

            df_items_init = pd.DataFrame(initial_rows)
            edited_df = st.data_editor(
                df_items_init,
                num_rows="dynamic",
                use_container_width=True,
                key="inv_items_editor",
                column_config={
                    "Description": st.column_config.TextColumn("Description / Uraian", required=True, width="large"),
                    "Quantity": st.column_config.NumberColumn("Quantity", min_value=0.01, step=1.0, required=True),
                    "Unit Price": st.column_config.NumberColumn(f"Unit Price ({edit_currency})", min_value=0.0, step=10.0, required=True),
                    "Subtotal": st.column_config.NumberColumn(f"Subtotal ({edit_currency})", disabled=True),
                }
            )

            # Recalculate Subtotal & Grand Total
            calculated_grand_total = 0.0
            valid_lines = []
            for _, r in edited_df.iterrows():
                desc = str(r.get("Description") or "").strip()
                if desc:
                    qty = float(r.get("Quantity") or 1.0)
                    price = float(r.get("Unit Price") or 0.0)
                    sub = qty * price
                    calculated_grand_total += sub

                    # Converted prices
                    price_booked = price * exchange_rate if convert_to_idr else price
                    sub_booked = qty * price_booked
                    valid_lines.append({
                        "description": desc,
                        "quantity": qty,
                        "unit_price_orig": price,
                        "unit_price_booked": price_booked,
                        "subtotal_orig": sub,
                        "subtotal_booked": sub_booked
                    })

            grand_total_booked = sum(l["subtotal_booked"] for l in valid_lines)

            c_gt1, c_gt2 = st.columns([3, 1])
            with c_gt2:
                if convert_to_idr:
                    st.metric("Total in Rupiah (IDR)", f"Rp {grand_total_booked:,.2f}", delta=f"Orig: {edit_currency} {calculated_grand_total:,.2f} @ Rp {exchange_rate:,.2f}")
                else:
                    st.metric("Total Invoice Amount", f"{edit_currency} {calculated_grand_total:,.2f}")

            # Show explicit IDR Conversion Breakdown table if foreign currency
            if is_foreign_currency and convert_to_idr:
                st.markdown("##### 💵 Converted Line Items in Rupiah (IDR):")
                conv_display_rows = []
                for idx, ln in enumerate(valid_lines, 1):
                    conv_display_rows.append({
                        "Item": ln["description"],
                        "Qty": f"{ln['quantity']:,.2f}",
                        f"Unit Price ({edit_currency})": f"{edit_currency} {ln['unit_price_orig']:,.2f}",
                        "Unit Price (IDR)": f"Rp {ln['unit_price_booked']:,.2f}",
                        "Subtotal (IDR)": f"Rp {ln['subtotal_booked']:,.2f}",
                    })
                st.dataframe(pd.DataFrame(conv_display_rows), use_container_width=True, hide_index=True)

            # --- LIVE ODOO PRE-SUBMISSION STATUS ---
            st.divider()
            st.subheader("🔍 Step 3: Odoo Pre-Submission Verification")
            with st.spinner("Checking Odoo records for vendor and products..."):
                try:
                    # 1. Check Vendor in Odoo
                    partner_results = odoo.search_read(
                        "res.partner",
                        domain=[["name", "ilike", edit_partner.strip()]],
                        fields=["id", "name", "company_id"],
                        limit=1,
                        company_id=inv_company_id
                    )
                    if partner_results:
                        p_rec = partner_results[0]
                        st.success(f"✓ **Existing Vendor Found in Odoo:** `{p_rec['name']}` (ID: `{p_rec['id']}`)")
                    else:
                        st.info(f"✨ **New Vendor will be automatically created in Odoo:** `{edit_partner.strip()}` (supplier_rank: 1)")

                    # 2. Check Products in Odoo
                    matched_prods = 0
                    new_prods = 0
                    for line in valid_lines:
                        p_search = odoo.search_read(
                            "product.product",
                            domain=[["name", "ilike", line["description"]]],
                            fields=["id", "name"],
                            limit=1,
                            company_id=inv_company_id
                        )
                        if p_search:
                            matched_prods += 1
                        else:
                            new_prods += 1

                    booking_curr_name = "Indonesian Rupiah (IDR)" if convert_to_idr else edit_currency
                    st.markdown(f"""
                    - **Products Check:** `{matched_prods}` existing product(s) matched | `{new_prods}` new product/service(s) will be created automatically in Odoo.
                    - **Booking Currency:** `{booking_curr_name}` ({rate_info_str})
                    - **Target Company:** `{inv_target_company}`
                    """)

                    # --- CREATE IN ODOO BUTTON ---
                    if st.button("🚀 Confirm & Create Bill in Odoo Accounting", type="primary", use_container_width=True, key="btn_create_inv_odoo"):
                        with st.spinner("Creating Vendor Bill and attaching document in Odoo..."):
                            # A. Get or Create Partner
                            if partner_results:
                                final_partner_id = partner_results[0]["id"]
                            else:
                                p_vals = {
                                    "name": edit_partner.strip(),
                                    "company_id": inv_company_id,
                                }
                                if chosen_move_type == "in_invoice":
                                    p_vals["supplier_rank"] = 1
                                else:
                                    p_vals["customer_rank"] = 1
                                final_partner_id = odoo.call("res.partner", "create", [p_vals], company_id=inv_company_id)

                            # B. Get or Create Products for each line (Auto-add new product if not found!)
                            move_lines_payload = []
                            for line in valid_lines:
                                p_search = odoo.search_read(
                                    "product.product",
                                    domain=[["name", "ilike", line["description"]]],
                                    fields=["id", "name"],
                                    limit=1,
                                    company_id=inv_company_id
                                )
                                if p_search:
                                    prod_id = p_search[0]["id"]
                                else:
                                    # Create new product / service with price in IDR
                                    new_prod_vals = {
                                        "name": line["description"],
                                        "type": "service",
                                        "purchase_ok": True,
                                        "sale_ok": False,
                                        "standard_price": float(line["unit_price_booked"]),
                                        "company_id": inv_company_id,
                                    }
                                    prod_id = odoo.call("product.product", "create", [new_prod_vals], company_id=inv_company_id)

                                # Format line description to record original currency if converted
                                if convert_to_idr:
                                    line_desc = f"{line['description']} [Orig: {edit_currency} {line['unit_price_orig']:,.2f} @ Kurs Rp {exchange_rate:,.2f}]"
                                else:
                                    line_desc = line["description"]

                                move_lines_payload.append((0, 0, {
                                    "product_id": prod_id,
                                    "name": line_desc,
                                    "quantity": float(line["quantity"]),
                                    "price_unit": float(line["unit_price_booked"]),
                                }))

                            # C. Currency handling (if not converted to IDR, set foreign currency in Odoo)
                            currency_id = None
                            if not convert_to_idr and is_foreign_currency:
                                c_recs = odoo.search_read("res.currency", domain=[["name", "=", edit_currency.upper()]], fields=["id"], limit=1)
                                if c_recs:
                                    currency_id = c_recs[0]["id"]

                            # D. Create account.move
                            narration_text = edit_notes.strip() or f"Bukti Kas Keluar - {edit_partner.strip()}"
                            if convert_to_idr:
                                narration_text += f" | Kurs: 1 {edit_currency} = Rp {exchange_rate:,.2f} on {edit_date} (Orig Total: {edit_currency} {calculated_grand_total:,.2f})"

                            move_vals = {
                                "move_type": chosen_move_type,
                                "partner_id": final_partner_id,
                                "invoice_date": edit_date.strip(),
                                "company_id": inv_company_id,
                                "ref": edit_ref.strip() or f"VOUCHER-{edit_date.strip()}",
                                "narration": narration_text,
                                "invoice_line_ids": move_lines_payload,
                            }
                            if currency_id:
                                move_vals["currency_id"] = currency_id
                            if edit_due_date.strip():
                                move_vals["invoice_date_due"] = edit_due_date.strip()

                            created_move_id = odoo.call("account.move", "create", [move_vals], company_id=inv_company_id)

                            # E. Attach original uploaded image to account.move
                            if "inv_uploaded_file" in st.session_state:
                                f_inv = st.session_state["inv_uploaded_file"]
                                try:
                                    odoo.attach_file(
                                        file_name=f_inv["name"],
                                        file_bytes=f_inv["bytes"],
                                        res_model="account.move",
                                        res_id=created_move_id,
                                        company_id=inv_company_id
                                    )
                                except Exception as e_att:
                                    st.warning(f"Note: Could not attach file: {e_att}")

                            # F. Auto-post if requested
                            if inv_auto_post:
                                try:
                                    odoo.call("account.move", "action_post", [[created_move_id]], company_id=inv_company_id)
                                except Exception as e_p:
                                    st.warning(f"Could not auto-post: {e_p}")

                            # G. Read created bill details
                            move_info = odoo.call(
                                "account.move", "read",
                                [created_move_id],
                                {"fields": ["name", "state", "amount_total", "company_id"]},
                                company_id=inv_company_id
                            )[0]

                            display_total_currency = "Rp" if convert_to_idr else edit_currency
                            st.balloons()
                            st.success("🎉 **Invoice / Vendor Bill Entered Successfully into Odoo Accounting!**")
                            st.markdown(f"""
                            - **Bill Reference / Name:** `{move_info.get('name') or '(Draft)'}` (ID: `{created_move_id}`)
                            - **Status:** `{move_info.get('state')}`
                            - **Company:** `{move_info.get('company_id')[1] if move_info.get('company_id') else inv_target_company}`
                            - **Total Booked:** **{display_total_currency} {move_info.get('amount_total', grand_total_booked):,.2f}**
                            - **Vendor:** `{edit_partner}` (ID: `{final_partner_id}`)
                            - **Document:** Original picture permanently attached to the bill in Odoo.
                            """)

                except Exception as e:
                    st.error(f"Error communicating with Odoo: {e}")

# ==============================================================================
# TAB 3: ODOO NATURAL LANGUAGE ASSISTANT (CHAT WITH ERP)
# ==============================================================================
with tab_assistant:
    st.subheader("💬 Ask Odoo in Plain English")
    st.caption("Ask questions, find customers, check stock, verify sales orders, or trigger actions (confirm order, check models, inspect fields).")

    # Define tools for AI Assistant
    def search_odoo_records(model: str, domain_json: str = "[]", fields_json: str = "[]", limit: int = 15) -> str:
        """Search records in Odoo by model name, domain JSON string, and optional fields list."""
        try:
            domain = json.loads(domain_json) if domain_json else []
            fields = json.loads(fields_json) if fields_json else None
            records = odoo.search_read(model, domain=domain, fields=fields, limit=limit, company_id=company_id_val)
            return json.dumps({"count": len(records), "records": records}, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def get_odoo_fields(model: str) -> str:
        """Get the field definitions (name, type, label, required) of an Odoo model."""
        try:
            fields = odoo.call(model, "fields_get", [], {"attributes": ["string", "type", "required", "readonly"]}, company_id=company_id_val)
            return json.dumps(fields, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def find_odoo_models(query: str) -> str:
        """Search installed Odoo models by technical name or label."""
        try:
            models_list = odoo.call("ir.model", "search_read", [[["model", "ilike", query]]], {"fields": ["model", "name"], "limit": 20}, company_id=company_id_val)
            return json.dumps(models_list, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def execute_odoo_method(model: str, record_id: int, method: str) -> str:
        """Execute a business method on a record, such as action_confirm or action_cancel on sale.order."""
        try:
            res = odoo.call(model, method, [[record_id]], company_id=company_id_val)
            return json.dumps({"status": "success", "result": res}, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    # Initialize chat history
    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = [
            {"role": "assistant", "content": "Hello! I am your Odoo ERP assistant. You can ask me to search sales orders, look up products, check customers, or run actions on orders."}
        ]

    for msg in st.session_state["chat_messages"]:
        st.chat_message(msg["role"]).write(msg["content"])

    user_query = st.chat_input("E.g., 'Show me the last 5 sales orders', 'Check order S00327', 'Search products for Astro'")
    if user_query:
        st.session_state["chat_messages"].append({"role": "user", "content": user_query})
        st.chat_message("user").write(user_query)

        with st.chat_message("assistant"):
            with st.spinner(f"Thinking and querying Odoo with {ai_provider}..."):
                try:
                    reply_text = ""
                    if ai_provider == "Google Gemini":
                        if not gemini_key:
                            st.error("Please enter a Google Gemini API Key.")
                            st.stop()
                        ai_client = genai.Client(api_key=gemini_key)
                        chat = ai_client.chats.create(
                            model=gemini_model,
                            config={
                                "tools": [search_odoo_records, get_odoo_fields, find_odoo_models, execute_odoo_method],
                                "system_instruction": (
                                    f"You are an expert Odoo ERP assistant connected to Kultiva and Alpha Mas Indo Odoo instance. "
                                    f"Current company context is ID: {company_id_val}. "
                                    f"Use the tools provided to query models like sale.order, res.partner, product.product, and ir.attachment. "
                                    f"Always format your replies cleanly with markdown tables and bullet points."
                                )
                            }
                        )
                        reply = chat.send_message(user_query)
                        reply_text = reply.text
                    else:
                        # Anthropic Claude
                        import anthropic
                        if not claude_key:
                            st.error("Please enter an Anthropic Claude API Key (sk-ant-...).")
                            st.stop()
                        cl_client = anthropic.Anthropic(api_key=claude_key)
                        claude_tools = [
                            {
                                "name": "search_odoo_records",
                                "description": "Search records in Odoo by model name, domain JSON string, and optional fields list",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "model": {"type": "string"},
                                        "domain_json": {"type": "string", "description": "Odoo domain as JSON string, e.g. '[[\"name\",\"ilike\",\"Astro\"]]'"},
                                        "fields_json": {"type": "string", "description": "Fields list as JSON string, e.g. '[\"name\",\"amount_total\"]'"},
                                        "limit": {"type": "integer"}
                                    },
                                    "required": ["model"]
                                }
                            },
                            {
                                "name": "get_odoo_fields",
                                "description": "Get field definitions of an Odoo model",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {"model": {"type": "string"}},
                                    "required": ["model"]
                                }
                            },
                            {
                                "name": "find_odoo_models",
                                "description": "Search installed Odoo models by query string",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {"query": {"type": "string"}},
                                    "required": ["query"]
                                }
                            },
                            {
                                "name": "execute_odoo_method",
                                "description": "Execute a business method on an Odoo record like action_confirm or action_cancel",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "model": {"type": "string"},
                                        "record_id": {"type": "integer"},
                                        "method": {"type": "string"}
                                    },
                                    "required": ["model", "record_id", "method"]
                                }
                            }
                        ]
                        
                        c_messages = [{"role": "user", "content": user_query}]
                        c_response = cl_client.messages.create(
                            model=claude_model,
                            max_tokens=4096,
                            tools=claude_tools,
                            system=f"You are an expert Odoo ERP assistant connected to Kultiva and Alpha Mas Indo Odoo instance. Current company context ID: {company_id_val}. Use the tools provided to query models like sale.order, res.partner, product.product, and format cleanly.",
                            messages=c_messages
                        )
                        
                        if c_response.stop_reason == "tool_use":
                            tool_results = []
                            for b in c_response.content:
                                if b.type == "tool_use":
                                    fn_name = b.name
                                    fn_args = b.input
                                    if fn_name == "search_odoo_records":
                                        t_out = search_odoo_records(fn_args.get("model"), fn_args.get("domain_json", "[]"), fn_args.get("fields_json", "[]"), fn_args.get("limit", 15))
                                    elif fn_name == "get_odoo_fields":
                                        t_out = get_odoo_fields(fn_args.get("model"))
                                    elif fn_name == "find_odoo_models":
                                        t_out = find_odoo_models(fn_args.get("query"))
                                    elif fn_name == "execute_odoo_method":
                                        t_out = execute_odoo_method(fn_args.get("model"), fn_args.get("record_id"), fn_args.get("method"))
                                    else:
                                        t_out = json.dumps({"error": f"Unknown tool {fn_name}"})
                                    tool_results.append({
                                        "type": "tool_result",
                                        "tool_use_id": b.id,
                                        "content": t_out
                                    })
                            c_messages.append({"role": "assistant", "content": c_response.content})
                            c_messages.append({"role": "user", "content": tool_results})
                            c_final = cl_client.messages.create(
                                model=claude_model,
                                max_tokens=4096,
                                tools=claude_tools,
                                messages=c_messages
                            )
                            reply_text = c_final.content[0].text
                        else:
                            reply_text = c_response.content[0].text

                    st.write(reply_text)
                    st.session_state["chat_messages"].append({"role": "assistant", "content": reply_text})
                except Exception as e:
                    err_msg = f"Error processing query: {e}"
                    st.error(err_msg)
                    st.session_state["chat_messages"].append({"role": "assistant", "content": err_msg})

# ==============================================================================
# TAB 3: ODOO RECORDS EXPLORER
# ==============================================================================
with tab_explorer:
    st.subheader("🔍 Explore Odoo Records Directly")
    
    col_exp1, col_exp2, col_exp3 = st.columns([1, 1, 2])
    with col_exp1:
        explore_model = st.selectbox(
            "Select Model",
            ["sale.order (Sales Orders)", "res.partner (Customers / Vendors)", "product.product (Products)", "stock.picking (Deliveries)"]
        )
        model_name = explore_model.split(" ")[0]
    with col_exp2:
        exp_limit = st.slider("Record Limit", min_value=5, max_value=100, value=20, step=5)
    with col_exp3:
        search_filter = st.text_input("Filter by Name / Reference", placeholder="E.g. S003, Astro, Fore, Seastar...")

    domain = []
    if search_filter:
        if model_name == "sale.order":
            domain = ["|", ["name", "ilike", search_filter], ["client_order_ref", "ilike", search_filter]]
        else:
            domain = [["name", "ilike", search_filter]]

    if st.button("🔄 Fetch Records from Odoo"):
        with st.spinner("Fetching data from Odoo..."):
            try:
                field_map = {
                    "sale.order": ["id", "name", "client_order_ref", "partner_id", "date_order", "amount_total", "state", "company_id"],
                    "res.partner": ["id", "name", "email", "phone", "city", "customer_rank", "company_id"],
                    "product.product": ["id", "name", "list_price", "barcode", "default_code", "sale_ok"],
                    "stock.picking": ["id", "name", "partner_id", "state", "scheduled_date", "origin"]
                }
                records = odoo.search_read(
                    model_name,
                    domain=domain,
                    fields=field_map.get(model_name),
                    limit=exp_limit,
                    order="id desc",
                    company_id=company_id_val
                )
                if records:
                    st.success(f"Found {len(records)} records in `{model_name}`:")
                    st.dataframe(pd.DataFrame(records), use_container_width=True)
                else:
                    st.info("No records found matching the filter.")
            except Exception as e:
                st.error(f"Error fetching records: {e}")


# ==============================================================================
# TAB 4: SALES ORDER EDITOR
# ==============================================================================
with tab_editor:
    st.subheader("✏️ Edit Existing Sales Order")
    st.caption(
        "Find a Sales Order by its Odoo reference (e.g. S00123) or customer PO number (e.g. P00478), "
        "then remove or modify line items directly."
    )

    # ── Search Section ────────────────────────────────────────────────────────
    ed_col1, ed_col2, ed_col3 = st.columns([2, 1, 1])
    with ed_col1:
        so_search_term = st.text_input(
            "Order Reference / Customer PO Number",
            placeholder="e.g. P00478 or S00327",
            key="so_search_term"
        )
    with ed_col2:
        so_search_company = st.selectbox(
            "Search in Company",
            ["Both Companies", "PT Alpha Mas Indo (ID: 8)", "PT Kultiva Indonesia Makmur (ID: 1)"],
            key="so_search_company"
        )
    with ed_col3:
        st.write("")
        st.write("")
        search_so_btn = st.button("🔎 Find Sales Order", type="primary", use_container_width=True, key="search_so_btn")

    if search_so_btn and so_search_term:
        st.session_state.pop("so_editor_order", None)
        st.session_state.pop("so_editor_lines", None)
        with st.spinner(f"Searching Odoo for '{so_search_term}'..."):
            try:
                search_domain = [
                    "|",
                    ["name", "ilike", so_search_term],
                    ["client_order_ref", "ilike", so_search_term]
                ]
                so_fields = ["id", "name", "client_order_ref", "partner_id", "date_order",
                             "amount_total", "state", "company_id", "order_line"]

                found_orders = []
                companies_to_search = []
                if "Alpha Mas" in so_search_company:
                    companies_to_search = [8]
                elif "Kultiva" in so_search_company:
                    companies_to_search = [1]
                else:
                    companies_to_search = [8, 1]

                for cid in companies_to_search:
                    results = odoo.search_read(
                        "sale.order",
                        domain=search_domain,
                        fields=so_fields,
                        limit=10,
                        order="id desc",
                        company_id=cid
                    )
                    for r in results:
                        r["_search_company_id"] = cid
                    found_orders.extend(results)

                # De-duplicate by id
                seen_ids = set()
                unique_orders = []
                for o in found_orders:
                    if o["id"] not in seen_ids:
                        seen_ids.add(o["id"])
                        unique_orders.append(o)

                if unique_orders:
                    st.session_state["so_editor_found"] = unique_orders
                    st.success(f"Found {len(unique_orders)} order(s).")
                else:
                    st.session_state["so_editor_found"] = []
                    st.warning(f"No Sales Order found matching '{so_search_term}' in Odoo.")

            except Exception as e:
                st.error(f"Error searching Odoo: {e}")

    # ── Order Selection ───────────────────────────────────────────────────────
    if st.session_state.get("so_editor_found"):
        found_orders = st.session_state["so_editor_found"]

        order_options = {
            f"{o['name']}  |  PO: {o.get('client_order_ref') or '—'}  |  "
            f"Customer: {o['partner_id'][1] if o.get('partner_id') else '—'}  |  "
            f"Status: {o['state']}  |  "
            f"Company: {o['company_id'][1] if o.get('company_id') else '?'}": o
            for o in found_orders
        }
        selected_label = st.selectbox(
            "Select Sales Order to Edit",
            list(order_options.keys()),
            key="so_editor_select"
        )
        selected_order = order_options[selected_label]

        load_lines_btn = st.button(
            f"📂 Load Line Items for {selected_order['name']}",
            use_container_width=True,
            key="load_lines_btn"
        )

        if load_lines_btn:
            with st.spinner("Loading order lines from Odoo..."):
                try:
                    so_company_id = (
                        selected_order["company_id"][0]
                        if selected_order.get("company_id") else
                        selected_order.get("_search_company_id", 1)
                    )
                    line_ids = selected_order.get("order_line", [])
                    if not line_ids:
                        st.info("This Sales Order has no line items.")
                    else:
                        lines = odoo.call(
                            "sale.order.line", "read",
                            [line_ids],
                            {"fields": ["id", "product_id", "name", "product_uom_qty",
                                        "price_unit", "price_subtotal", "state"]},
                            company_id=so_company_id
                        )
                        st.session_state["so_editor_order"] = selected_order
                        st.session_state["so_editor_lines"] = lines
                        st.session_state["so_editor_company_id"] = so_company_id
                except Exception as e:
                    st.error(f"Error loading order lines: {e}")

    # ── Line Item Editor ──────────────────────────────────────────────────────
    if st.session_state.get("so_editor_lines") is not None:
        order = st.session_state["so_editor_order"]
        lines = st.session_state["so_editor_lines"]
        so_company_id = st.session_state.get("so_editor_company_id", 1)

        st.divider()
        st.subheader(f"📋 Line Items — {order['name']}")

        # Order summary
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Order No.", order["name"])
        m2.metric("Customer PO Ref", order.get("client_order_ref") or "—")
        m3.metric("Status", order["state"])
        m4.metric("Total", f"Rp {order.get('amount_total', 0):,.0f}")

        # State warning
        order_state = order.get("state", "draft")
        if order_state == "sale":
            st.warning(
                "⚠️ This order is **confirmed** (`sale`). "
                "It will be **reset to draft** automatically before removing lines, then re-confirmed."
            )
        elif order_state == "done":
            st.error("❌ This order is **locked/done** and cannot be edited.")
        elif order_state == "cancel":
            st.error("❌ This order is **cancelled** and cannot be edited.")

        # Build line table with checkboxes
        st.write("**Select lines to REMOVE:**")

        line_rows = []
        for ln in lines:
            prod_name = ln["product_id"][1] if ln.get("product_id") else ln.get("name", "—")
            line_rows.append({
                "ID": ln["id"],
                "Product": prod_name,
                "Description": ln.get("name", ""),
                "Qty": ln.get("product_uom_qty", 0),
                "Unit Price": f"Rp {ln.get('price_unit', 0):,.0f}",
                "Subtotal": f"Rp {ln.get('price_subtotal', 0):,.0f}",
            })

        df_lines = pd.DataFrame(line_rows)

        # Use multiselect for line removal
        line_display_opts = {
            f"[ID:{row['ID']}] {row['Product']}  (Qty: {row['Qty']})": row["ID"]
            for row in line_rows
        }
        lines_to_remove_labels = st.multiselect(
            "Lines to Remove (select one or more):",
            list(line_display_opts.keys()),
            key="lines_to_remove_select"
        )
        lines_to_remove_ids = [line_display_opts[lbl] for lbl in lines_to_remove_labels]

        # Show current line table
        st.dataframe(df_lines.drop(columns=["ID"]), use_container_width=True, hide_index=True)

        if lines_to_remove_ids:
            st.warning(f"⚠️ You are about to **permanently delete {len(lines_to_remove_ids)} line(s)** from {order['name']}. This cannot be undone.")

            remove_btn = st.button(
                f"🗑️ Remove {len(lines_to_remove_ids)} Selected Line(s) from {order['name']}",
                type="primary",
                use_container_width=True,
                key="remove_lines_btn"
            )

            if remove_btn:
                if order_state in ("done", "cancel"):
                    st.error("Cannot edit a done or cancelled order.")
                else:
                    with st.spinner("Removing line items from Odoo..."):
                        try:
                            # If confirmed, reset to draft first
                            if order_state == "sale":
                                st.info("Resetting order to draft...")
                                odoo.call(
                                    "sale.order", "action_cancel",
                                    [[order["id"]]],
                                    company_id=so_company_id
                                )
                                odoo.call(
                                    "sale.order", "action_draft",
                                    [[order["id"]]],
                                    company_id=so_company_id
                                )

                            # Delete the selected lines
                            odoo.call(
                                "sale.order.line", "unlink",
                                [lines_to_remove_ids],
                                company_id=so_company_id
                            )

                            # Re-confirm if it was confirmed before
                            if order_state == "sale":
                                st.info("Re-confirming order...")
                                odoo.call(
                                    "sale.order", "action_confirm",
                                    [[order["id"]]],
                                    company_id=so_company_id
                                )

                            st.success(f"✅ Successfully removed {len(lines_to_remove_ids)} line(s) from {order['name']}!")

                            # Reload the updated lines
                            updated_order = odoo.search_read(
                                "sale.order",
                                domain=[["id", "=", order["id"]]],
                                fields=["id", "name", "client_order_ref", "partner_id", "date_order",
                                        "amount_total", "state", "company_id", "order_line"],
                                limit=1,
                                company_id=so_company_id
                            )
                            if updated_order:
                                updated_line_ids = updated_order[0].get("order_line", [])
                                if updated_line_ids:
                                    updated_lines = odoo.call(
                                        "sale.order.line", "read",
                                        [updated_line_ids],
                                        {"fields": ["id", "product_id", "name", "product_uom_qty",
                                                    "price_unit", "price_subtotal", "state"]},
                                        company_id=so_company_id
                                    )
                                else:
                                    updated_lines = []
                                st.session_state["so_editor_order"] = updated_order[0]
                                st.session_state["so_editor_lines"] = updated_lines
                                st.rerun()

                        except Exception as e:
                            st.error(f"Error removing lines: {e}")
        else:
            st.info("Select one or more lines above to enable the remove button.")

        # ── Quick Remove by Product Name ──────────────────────────────────────
        st.divider()
        with st.expander("🔍 Quick Remove by Product Name (keyword match)"):
            st.caption("Type keywords to auto-select matching lines. Useful for removing by product name fragments.")
            keyword_input = st.text_input(
                "Product name keywords (comma-separated)",
                placeholder="e.g. Tempe BBQ, Banana Seasalt, Non GMO",
                key="quick_remove_keywords"
            )
            if keyword_input:
                keywords = [k.strip().lower() for k in keyword_input.split(",") if k.strip()]
                matched_lines = []
                for ln in lines:
                    prod_name = (ln["product_id"][1] if ln.get("product_id") else ln.get("name", "")).lower()
                    desc = ln.get("name", "").lower()
                    if any(kw in prod_name or kw in desc for kw in keywords):
                        matched_lines.append(ln)

                if matched_lines:
                    st.write(f"**{len(matched_lines)} line(s) matched:**")
                    for ml in matched_lines:
                        pname = ml["product_id"][1] if ml.get("product_id") else ml.get("name", "—")
                        st.markdown(f"- `[ID:{ml['id']}]` **{pname}** — Qty: {ml.get('product_uom_qty', 0)}")

                    if order_state not in ("done", "cancel"):
                        quick_remove_btn = st.button(
                            f"🗑️ Remove These {len(matched_lines)} Matched Line(s)",
                            type="primary",
                            key="quick_remove_btn"
                        )
                        if quick_remove_btn:
                            matched_ids = [ml["id"] for ml in matched_lines]
                            with st.spinner("Removing matched lines..."):
                                try:
                                    if order_state == "sale":
                                        odoo.call("sale.order", "action_cancel", [[order["id"]]], company_id=so_company_id)
                                        odoo.call("sale.order", "action_draft", [[order["id"]]], company_id=so_company_id)

                                    odoo.call("sale.order.line", "unlink", [matched_ids], company_id=so_company_id)

                                    if order_state == "sale":
                                        odoo.call("sale.order", "action_confirm", [[order["id"]]], company_id=so_company_id)

                                    st.success(f"✅ Removed {len(matched_ids)} line(s) successfully!")

                                    # Reload
                                    updated_order = odoo.search_read(
                                        "sale.order",
                                        domain=[["id", "=", order["id"]]],
                                        fields=["id", "name", "client_order_ref", "partner_id", "date_order",
                                                "amount_total", "state", "company_id", "order_line"],
                                        limit=1,
                                        company_id=so_company_id
                                    )
                                    if updated_order:
                                        updated_line_ids = updated_order[0].get("order_line", [])
                                        updated_lines = odoo.call(
                                            "sale.order.line", "read",
                                            [updated_line_ids],
                                            {"fields": ["id", "product_id", "name", "product_uom_qty",
                                                        "price_unit", "price_subtotal", "state"]},
                                            company_id=so_company_id
                                        ) if updated_line_ids else []
                                        st.session_state["so_editor_order"] = updated_order[0]
                                        st.session_state["so_editor_lines"] = updated_lines
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")
                    else:
                        st.error("Order is in done/cancelled state — cannot edit.")
                else:
                    st.info("No lines matched those keywords.")

        # ── Add Line Item ──────────────────────────────────────────────────────
        st.divider()
        with st.expander("➕ Add a New Line Item to this Order"):
            if order_state in ("done", "cancel"):
                st.error("Cannot add lines to a done or cancelled order.")
            else:
                add_col1, add_col2, add_col3 = st.columns([3, 1, 1])
                with add_col1:
                    new_product_search = st.text_input("Search Product by Name", key="add_line_product_search")
                with add_col2:
                    new_qty = st.number_input("Quantity", min_value=1.0, value=1.0, step=1.0, key="add_line_qty")
                with add_col3:
                    new_price = st.number_input("Unit Price (Rp)", min_value=0.0, value=0.0, step=100.0, key="add_line_price")

                if new_product_search:
                    with st.spinner("Searching products..."):
                        try:
                            prod_results = odoo.search_read(
                                "product.product",
                                domain=[["sale_ok", "=", True], ["name", "ilike", new_product_search]],
                                fields=["id", "name", "list_price"],
                                limit=10,
                                company_id=so_company_id
                            )
                            if prod_results:
                                prod_map = {f"[{p['id']}] {p['name']} (Rp {p['list_price']:,.0f})": p for p in prod_results}
                                selected_prod_label = st.selectbox("Select Product", list(prod_map.keys()), key="add_line_prod_select")
                                selected_prod = prod_map[selected_prod_label]

                                if st.button("➕ Add Line to Order", type="primary", key="add_line_btn"):
                                    with st.spinner("Adding line to order..."):
                                        try:
                                            price_to_use = new_price if new_price > 0 else selected_prod["list_price"]
                                            if order_state == "sale":
                                                odoo.call("sale.order", "action_cancel", [[order["id"]]], company_id=so_company_id)
                                                odoo.call("sale.order", "action_draft", [[order["id"]]], company_id=so_company_id)

                                            odoo.call("sale.order.line", "create", [{
                                                "order_id": order["id"],
                                                "product_id": selected_prod["id"],
                                                "name": selected_prod["name"],
                                                "product_uom_qty": new_qty,
                                                "price_unit": price_to_use,
                                            }], company_id=so_company_id)

                                            if order_state == "sale":
                                                odoo.call("sale.order", "action_confirm", [[order["id"]]], company_id=so_company_id)

                                            st.success(f"✅ Added {new_qty}x {selected_prod['name']} to {order['name']}!")
                                            # Reload
                                            updated_order = odoo.search_read(
                                                "sale.order",
                                                domain=[["id", "=", order["id"]]],
                                                fields=["id", "name", "client_order_ref", "partner_id", "date_order",
                                                        "amount_total", "state", "company_id", "order_line"],
                                                limit=1,
                                                company_id=so_company_id
                                            )
                                            if updated_order:
                                                updated_line_ids = updated_order[0].get("order_line", [])
                                                updated_lines = odoo.call(
                                                    "sale.order.line", "read",
                                                    [updated_line_ids],
                                                    {"fields": ["id", "product_id", "name", "product_uom_qty",
                                                                "price_unit", "price_subtotal", "state"]},
                                                    company_id=so_company_id
                                                ) if updated_line_ids else []
                                                st.session_state["so_editor_order"] = updated_order[0]
                                                st.session_state["so_editor_lines"] = updated_lines
                                                st.rerun()
                                        except Exception as e:
                                            st.error(f"Error adding line: {e}")
                            else:
                                st.info("No products found. Try a different search term.")
                        except Exception as e:
                            st.error(f"Error searching products: {e}")
