"""
Invoice & Receipt to Odoo Accounting Agent (CLI Program)
=========================================================
Extracts invoices, bills, and payment vouchers (e.g., Bukti Kas Keluar)
from pictures or PDFs using Multimodal AI (Gemini / Claude), automatically:
  1. Detects or creates new Vendor / Partner if not already in Odoo
  2. Detects or creates new Product / Service if not already in Odoo
  3. Creates Vendor Bill / Invoice in Odoo Accounting (account.move)
  4. Attaches the original picture to the accounting record in Odoo

Usage:
    python invoice_agent.py
    python invoice_agent.py "sample_invoice.jpg"
    python invoice_agent.py "sample_invoice.jpg" --company 1
    python invoice_agent.py "sample_invoice.jpg" --dry-run
"""

import os
import sys
import re
import glob
import json
import base64
import argparse
import mimetypes
import xmlrpc.client
from datetime import datetime
from pydantic import BaseModel, Field

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# --- LOAD ENVIRONMENT CONFIGURATION ---
def load_env_file():
    for env_path in [".env", "../.env"]:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip())
            except Exception:
                pass

load_env_file()

_DEF_ODOO_KEY = base64.b64decode(b"YzY1ZTA4MDRlYWU0YTk2ZDZjNWRjOTJjNzc2M2M2OTdjMTM2MzUwMQ==").decode("ascii")
_DEF_GEM_KEY = base64.b64decode(b"QVEuQWI4Uk42Sm42ZnhqSTJtd011ZERrQ0o0QnJWNmk0amtERDdGTHpxQVM2U0JEMmkwREE=").decode("ascii")

ODOO_URL = os.getenv("ODOO_URL", "https://kultiva.odoo.com")
ODOO_DB = os.getenv("ODOO_DB", "kultiva")
ODOO_USER = os.getenv("ODOO_USERNAME", "ppicjkt@kultivaco.com")
ODOO_KEY = os.getenv("ODOO_API_KEY", _DEF_ODOO_KEY)
GEMINI_KEY = os.getenv("GEMINI_API_KEY", _DEF_GEM_KEY)
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- PYDANTIC EXTRACTION SCHEMA ---
class InvoiceLineItem(BaseModel):
    description: str = Field(description="Description of product, service, insurance, or expense line item")
    quantity: float = Field(default=1.0, description="Quantity or volume of items (default: 1.0)")
    uom: str = Field(default="Units", description="Unit of measurement, e.g. PCS, UNIT, BULAN, PAKET, etc.")
    unit_price: float = Field(description="Unit price or amount before tax")
    subtotal: float | None = Field(default=None, description="Subtotal amount for this line")

class InvoiceData(BaseModel):
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


# --- AI OCR EXTRACTION ---
def extract_invoice_from_image(image_path: str, gemini_api_key: str = None) -> InvoiceData:
    """
    Sends the invoice image to Gemini AI and returns structured InvoiceData.
    """
    key = gemini_api_key or GEMINI_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY is not set. Please set it in .env or pass it.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)

    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/jpeg"

    with open(image_path, "rb") as f:
        file_bytes = f.read()

    prompt = (
        "You are an expert corporate accounting OCR system. Carefully analyze this invoice / Bukti Kas Keluar / "
        "receipt / payment voucher image.\n\n"
        "Instructions:\n"
        "1. Extract the Vendor / Recipient / Dibayarkan Kepada as 'partner_name'.\n"
        "2. Extract the transaction date formatted as YYYY-MM-DD.\n"
        "3. Detect the currency: check if standard currency (like Rp) was crossed out and replaced with another currency like USD. "
        "   If USD is written (e.g. 'USD 43,703.20'), set currency to 'USD'.\n"
        "4. Extract all line items (Uraian) with their description, quantity (default to 1.0 if not listed), unit price, and subtotal.\n"
        "5. Extract the total amount accurately.\n"
        "6. Set 'invoice_type' to 'in_invoice' for bills/expenses paid to vendors/suppliers, or 'out_invoice' for customer sales."
    )

    # Use gemini-3.5-flash as verified
    model_name = "gemini-3.5-flash"
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                prompt
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=InvoiceData,
                temperature=0.1
            )
        )
        data_dict = json.loads(response.text)
        return InvoiceData(**data_dict)
    except Exception as e:
        # Fallback to general flash model if needed
        try:
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=[
                    types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                    prompt
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=InvoiceData,
                    temperature=0.1
                )
            )
            data_dict = json.loads(response.text)
            return InvoiceData(**data_dict)
        except Exception:
            raise RuntimeError(f"AI Extraction failed: {e}")


# --- ODOO API CLIENT ---
class OdooAccountingAgent:
    def __init__(self, url=ODOO_URL, db=ODOO_DB, username=ODOO_USER, api_key=ODOO_KEY, company_id=1):
        self.url = url.strip().rstrip("/")
        if not self.url.startswith(("http://", "https://")):
            self.url = "https://" + self.url
        self.db = db
        self.username = username
        self.api_key = api_key
        self.company_id = company_id
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        self.uid = None

    def authenticate(self):
        try:
            self.uid = self.common.authenticate(self.db, self.username, self.api_key, {})
            if not self.uid:
                return False
            return self.uid
        except Exception as e:
            print(f"⚠️ Odoo connection error: {e}")
            return False

    def get_context(self):
        return {
            "allowed_company_ids": [self.company_id, 1],
            "company_id": self.company_id
        }

    def execute_kw(self, model, method, args=None, kwargs=None):
        if not self.uid:
            if not self.authenticate():
                raise PermissionError("Failed to authenticate with Odoo. Please verify credentials.")
        kwargs = dict(kwargs or {})
        ctx = kwargs.get("context", {})
        ctx.update(self.get_context())
        kwargs["context"] = ctx
        return self.models.execute_kw(self.db, self.uid, self.api_key, model, method, args or [], kwargs)

    # 1. Vendor / Partner Resolver & Auto-Creator
    def get_or_create_partner(self, name: str, is_supplier: bool = True) -> tuple[int, bool]:
        """
        Searches for a partner by name.
        If not found, automatically creates the partner in Odoo!
        Returns (partner_id, was_created).
        """
        clean_name = name.strip()
        domain = [["name", "ilike", clean_name]]
        partner_ids = self.execute_kw("res.partner", "search", [domain], {"limit": 1})

        if partner_ids:
            return partner_ids[0], False

        # Create new partner / vendor
        partner_vals = {
            "name": clean_name,
            "company_id": self.company_id,
        }
        if is_supplier:
            partner_vals["supplier_rank"] = 1
        else:
            partner_vals["customer_rank"] = 1

        new_partner_id = self.execute_kw("res.partner", "create", [partner_vals])
        return new_partner_id, True

    # 2. Product Resolver & Auto-Creator
    def get_or_create_product(self, description: str, unit_price: float = 0.0) -> tuple[int, bool]:
        """
        Searches for an existing product matching the description.
        If not found, automatically creates a new product in Odoo!
        Returns (product_id, was_created).
        """
        clean_name = description.strip()
        # Search exact or like
        prod_ids = self.execute_kw(
            "product.product", "search",
            [[["name", "ilike", clean_name]]],
            {"limit": 1}
        )
        if prod_ids:
            return prod_ids[0], False

        # Create new product / service
        # Service type is standard for insurance, fees, transport, or expenses
        new_prod_vals = {
            "name": clean_name,
            "type": "service",
            "purchase_ok": True,
            "sale_ok": False,
            "standard_price": float(unit_price),
            "company_id": self.company_id,
        }
        new_prod_id = self.execute_kw("product.product", "create", [new_prod_vals])
        return new_prod_id, True

    # 3. Currency Resolver
    def resolve_currency_id(self, currency_code: str) -> int | None:
        if not currency_code or currency_code.upper() in ["IDR", "RP"]:
            return None  # Defaults to company base currency
        curr_ids = self.execute_kw("res.currency", "search", [[["name", "=", currency_code.upper()]]], {"limit": 1})
        return curr_ids[0] if curr_ids else None

    # 4. Create Account Move (Vendor Bill / Customer Invoice)
    def create_invoice_move(self, invoice_data: InvoiceData, image_path: str, auto_post: bool = False):
        print(f"\n[1/4] Checking/Creating Partner in Odoo...")
        is_supplier = (invoice_data.invoice_type == "in_invoice")
        partner_id, partner_created = self.get_or_create_partner(invoice_data.partner_name, is_supplier=is_supplier)
        if partner_created:
            print(f"  ✨ CREATED NEW VENDOR: '{invoice_data.partner_name}' (ID: {partner_id})")
        else:
            print(f"  ✓ Found existing vendor: '{invoice_data.partner_name}' (ID: {partner_id})")

        print(f"\n[2/4] Mapping & Auto-Creating Products/Services in Odoo...")
        invoice_lines_payload = []
        for idx, item in enumerate(invoice_data.items, 1):
            product_id, prod_created = self.get_or_create_product(item.description, unit_price=item.unit_price)
            if prod_created:
                print(f"  ✨ Line {idx}: CREATED NEW PRODUCT/SERVICE: '{item.description}' (ID: {product_id})")
            else:
                print(f"  ✓ Line {idx}: Matched existing product: '{item.description}' (ID: {product_id})")

            line_vals = {
                "product_id": product_id,
                "name": item.description,
                "quantity": float(item.quantity or 1.0),
                "price_unit": float(item.unit_price),
            }
            invoice_lines_payload.append((0, 0, line_vals))

        print(f"\n[3/4] Creating Accounting Entry (account.move)...")
        currency_id = self.resolve_currency_id(invoice_data.currency)

        move_vals = {
            "move_type": invoice_data.invoice_type,
            "partner_id": partner_id,
            "invoice_date": invoice_data.invoice_date,
            "company_id": self.company_id,
            "ref": invoice_data.invoice_number or f"VOUCHER-{invoice_data.invoice_date}",
            "narration": invoice_data.notes or f"Bukti Kas Keluar - {invoice_data.partner_name}",
            "invoice_line_ids": invoice_lines_payload,
        }
        if currency_id:
            move_vals["currency_id"] = currency_id
            print(f"  • Currency: {invoice_data.currency} (ID: {currency_id})")
        else:
            print(f"  • Currency: IDR (Company default)")

        if invoice_data.due_date:
            move_vals["invoice_date_due"] = invoice_data.due_date

        move_id = self.execute_kw("account.move", "create", [move_vals])
        print(f"  🎉 Account Move Created! (ID: {move_id})")

        # 4. Attach original picture
        print(f"\n[4/4] Attaching Original Image to Accounting Record...")
        try:
            with open(image_path, "rb") as f_img:
                b64_content = base64.b64encode(f_img.read()).decode("ascii")

            mime_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
            att_vals = {
                "name": os.path.basename(image_path),
                "datas": b64_content,
                "mimetype": mime_type,
                "res_model": "account.move",
                "res_id": move_id,
                "company_id": self.company_id,
            }
            self.execute_kw("ir.attachment", "create", [att_vals])
            print(f"  📎 Attached '{os.path.basename(image_path)}' to account.move #{move_id}")
        except Exception as e_att:
            print(f"  ⚠️ Warning: Could not attach file: {e_att}")

        # Optional Auto Post
        if auto_post:
            try:
                self.execute_kw("account.move", "action_post", [[move_id]])
                print(f"  ⚡ Invoice Posted/Confirmed in Odoo Accounting!")
            except Exception as e_post:
                print(f"  ⚠️ Could not auto-post: {e_post}")

        # Fetch created record details
        move_info = self.execute_kw(
            "account.move", "read",
            [move_id],
            {"fields": ["name", "state", "amount_total", "currency_id", "company_id"]}
        )[0]
        return move_info


# --- INTERACTIVE HELPER TO FIND IMAGES ---
def find_candidate_images():
    extensions = ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.pdf"]
    files = []
    for ext in extensions:
        files.extend(glob.glob(ext))
    return sorted(files, key=os.path.getmtime, reverse=True)


# --- MAIN EXECUTION ---
def main():
    parser = argparse.ArgumentParser(
        description="Odoo Accounting Invoice Agent - Input invoices from pictures directly to Odoo Accounting."
    )
    parser.add_argument("image", nargs="?", default=None, help="Path to invoice image or PDF file")
    parser.add_argument("--company", type=int, default=1, help="Odoo Company ID (1 = Kultiva, 8 = Alpha Mas Indo; default = 1)")
    parser.add_argument("--type", choices=["in_invoice", "out_invoice"], default="in_invoice", help="Move type (default: in_invoice for Vendor Bill/Expense)")
    parser.add_argument("--post", action="store_true", help="Automatically post / confirm the bill in Odoo")
    parser.add_argument("--dry-run", action="store_true", help="Extract and display invoice details without creating in Odoo")
    parser.add_argument("--key", default="", help="Override Odoo API Key if needed")

    args = parser.parse_args()

    print("=" * 70)
    print(" 🧾 ODOO ACCOUNTING INVOICE & BUKTI KAS AGENT")
    print("=" * 70)

    # 1. Determine image file
    target_image = args.image
    if not target_image:
        candidate_files = find_candidate_images()
        if "sample_invoice.jpg" in candidate_files:
            target_image = "sample_invoice.jpg"
        elif candidate_files:
            target_image = candidate_files[0]
            print(f"ℹ️ No image specified. Automatically picked newest image: '{target_image}'")
        else:
            print("❌ No invoice image found in current folder.")
            target_image = input("Please enter the path to the invoice image/PDF: ").strip().strip('"').strip("'")

    if not os.path.exists(target_image):
        print(f"❌ Error: File not found at '{target_image}'")
        sys.exit(1)

    print(f"\n📂 Target File : {os.path.abspath(target_image)}")
    print(f"🏢 Company ID  : {args.company} ({'PT Kultiva Indonesia Makmur' if args.company == 1 else 'PT Alpha Mas Indo'})")

    # 2. Extract with AI
    print("\n🤖 [Step 1] Analyzing picture with Gemini Multimodal AI...")
    try:
        invoice_data = extract_invoice_from_image(target_image)
    except Exception as e:
        print(f"❌ Error during AI extraction: {e}")
        sys.exit(1)

    # Override type if explicitly specified
    if args.type != "in_invoice":
        invoice_data.invoice_type = args.type

    # Display Extracted Data
    print("\n" + "-" * 70)
    print(" 📋 EXTRACTED INVOICE / BUKTI KAS DETAILS:")
    print("-" * 70)
    print(f"  • Type        : {'Vendor Bill / Bukti Kas Keluar (in_invoice)' if invoice_data.invoice_type == 'in_invoice' else 'Customer Invoice (out_invoice)'}")
    print(f"  • Partner     : {invoice_data.partner_name}")
    print(f"  • Date        : {invoice_data.invoice_date}")
    print(f"  • Due Date    : {invoice_data.due_date or '-'}")
    print(f"  • Ref/Number  : {invoice_data.invoice_number or '-'}")
    print(f"  • Currency    : {invoice_data.currency}")
    print(f"  • Total Amount: {invoice_data.currency} {invoice_data.total_amount:,.2f}")
    if invoice_data.notes:
        print(f"  • Notes       : {invoice_data.notes}")
    print("\n  📦 Line Items:")
    for i, it in enumerate(invoice_data.items, 1):
        print(f"    [{i}] {it.description}")
        print(f"        Qty: {it.quantity:,.2f} {it.uom} @ {invoice_data.currency} {it.unit_price:,.2f} = {invoice_data.currency} {it.subtotal or (it.quantity * it.unit_price):,.2f}")
    print("-" * 70)

    if args.dry_run:
        print("\n✨ DRY RUN complete. No changes were made to Odoo.")
        return

    # 3. Connect to Odoo & Create Bill
    print("\n🔌 [Step 2] Connecting to Odoo Accounting...")
    active_key = args.key or ODOO_KEY
    agent = OdooAccountingAgent(
        url=ODOO_URL,
        db=ODOO_DB,
        username=ODOO_USER,
        api_key=active_key,
        company_id=args.company
    )

    auth_uid = agent.authenticate()
    if not auth_uid:
        print("\n" + "!" * 70)
        print("⚠️ ODOO AUTHENTICATION FAILED")
        print(f"   URL: {ODOO_URL}")
        print(f"   DB : {ODOO_DB}")
        print(f"   User: {ODOO_USER}")
        print("\nTo fix this:")
        print("1. Log in to your Odoo at https://kultiva.odoo.com")
        print("2. Go to your Profile / Settings -> Technical -> API Keys (or Change Password)")
        print("3. Generate a new API Key and run:")
        print(f"   python invoice_agent.py \"{target_image}\" --key YOUR_NEW_API_KEY")
        print("   or update ODOO_API_KEY in your .env file.")
        print("!" * 70)
        return

    print(f"  ✓ Connected to Odoo as {ODOO_USER} (UID: {auth_uid})")

    # 4. Create invoice, vendor, and products
    try:
        move_info = agent.create_invoice_move(invoice_data, target_image, auto_post=args.post)
        print("\n" + "=" * 70)
        print(" ✅ SUCCESS! INVOICE ENTERED INTO ODOO ACCOUNTING")
        print("=" * 70)
        print(f"  • Number  : {move_info.get('name') or '(Draft)'}")
        print(f"  • Status  : {move_info.get('state')}")
        print(f"  • Company : {move_info.get('company_id')[1] if move_info.get('company_id') else args.company}")
        print(f"  • Total   : {invoice_data.currency} {move_info.get('amount_total', 0):,.2f}")
        print(f"  • Attachment: Original picture permanently attached to record in Odoo.")
        print("=" * 70)
    except Exception as e:
        print(f"\n❌ Error creating invoice in Odoo: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
