# 📦 Purchase Order to Odoo Sales Order AI Agent

An automated AI Agent that reads Purchase Orders (PDFs and Images) using Google Gemini, extracts line items and delivery metadata, matches items to existing Odoo products scoped by customer brand, and creates Sales Orders in the correct Odoo company via XML-RPC.

---

## 🌟 Key Features

- **Document Extraction**: Supports **PDF, JPEG, JPG, and PNG** Purchase Orders using Gemini AI.
- **Smart Multi-Company Routing**:
  - **Astro** & **Fore** orders → Routed to `PT Alpha Mas Indo` (Company ID 8)
  - **All other clients** (Seastar, Woh, Rava, Sangla, etc.) → Routed to `PT Kultiva Indonesia Makmur` (Company ID 1)
- **Scoped Product Matching**: Excludes raw materials (pouches, packaging boxes) and matches products strictly within the client's own brand catalog to prevent cross-brand errors.
- **Interactive Web Interface**: Streamlit web application with drag-and-drop document upload, visual table preview, and 1-click order creation.
- **Duplicate Prevention**: Checks for existing Sales Orders by PO reference before creating to avoid double orders.

---

## 🚀 Quick Start (Local Run)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Web Application
```bash
streamlit run app.py
```
Or double-click `start_web.bat` on Windows. The app will open at `http://localhost:8501`.

---

## ☁️ Deployment Guide

### Deploying on Streamlit Community Cloud (Recommended & Free)
1. Push this repository to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io/) and click **New app**.
3. Select your repository, branch (`main`), and set the main file path to `app.py`.
4. In **Advanced settings > Secrets**, paste your credentials:
   ```toml
   ODOO_URL = "https://kultiva.odoo.com"
   ODOO_DB = "kultiva"
   ODOO_USERNAME = "ppicjkt@kultivaco.com"
   ODOO_API_KEY = "your_odoo_api_key"
   GEMINI_API_KEY = "your_gemini_api_key"
   ```
5. Click **Deploy**!

---

## 📁 Repository Structure

```
├── app.py              # Streamlit Web Application
├── AI-Agent.py         # Standalone CLI / Batch Script version
├── start_web.bat       # Windows 1-click launcher
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variables template
├── .gitignore          # Excludes temporary & private files
└── README.md           # Documentation
```

