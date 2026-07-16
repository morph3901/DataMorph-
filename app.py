"""
Data Funnel Engine
==================
A dual-purpose Micro-SaaS Streamlit app:
  1. Bulk Resume Parser (for Recruiters) - extracts structured candidate
     data from PDF resumes using OpenAI.
  2. E-Commerce CSV Mapper (for Store Owners) - maps a messy supplier CSV
     to a Shopify-ready product import file, with markup pricing.

------------------------------------------------------------------------
INSTALLATION
------------------------------------------------------------------------
    pip install streamlit pandas openai pdfplumber

------------------------------------------------------------------------
SETUP
------------------------------------------------------------------------
Set your OpenAI API key as an environment variable before running:

    # macOS / Linux
    export OPENAI_API_KEY="sk-..."

    # Windows (PowerShell)
    $env:OPENAI_API_KEY="sk-..."

------------------------------------------------------------------------
RUN
------------------------------------------------------------------------
    streamlit run app.py
------------------------------------------------------------------------
"""

import io
import os
import json
import logging

import pandas as pd
import streamlit as st
import pdfplumber
from openai import OpenAI

from license_db import is_key_valid  # shared store, also written to by webhook_server.py

# --------------------------------------------------------------------------
# Logging setup - useful for debugging API/parsing failures without
# crashing the Streamlit app itself.
# --------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("data_funnel_engine")

# --------------------------------------------------------------------------
# Page configuration - dark theme is Streamlit's default when the user's
# system/browser is set to dark mode, or when configured in
# .streamlit/config.toml. We keep the layout wide and minimal here.
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="Data Funnel Engine",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------
# OpenAI client initialization
# --------------------------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    # We don't crash the app - the Resume Parser tool will simply show
    # a clear warning if the user tries to use it without a key set.
    logger.warning("OPENAI_API_KEY environment variable is not set.")


# ==========================================================================
# SESSION STATE INITIALIZATION
# ==========================================================================
# We use session_state so that processed data survives Streamlit re-runs
# (e.g. when the user toggles a checkbox or clicks a different button).
def init_session_state():
    defaults = {
        "resume_df": None,
        "shopify_df": None,
        "resume_paid_unlock": False,
        "shopify_paid_unlock": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()


# ==========================================================================
# SHARED HELPER: PAYWALL + DOWNLOAD SECTION
# ==========================================================================
def render_paywall_section(df: pd.DataFrame, unlock_key: str, filename: str):
    """
    Renders the shared preview / paywall / download UI block for a given
    processed DataFrame.

    Args:
        df: The processed DataFrame to preview/download.
        unlock_key: The session_state key that tracks whether this
                    particular tool's download has been "unlocked".
        filename: The filename to use for the downloaded CSV.
    """
    st.subheader("Preview Processed Data")
    st.dataframe(df.head(5), use_container_width=True)

    st.warning(
        "🔒 Your file is ready! To download the full dataset, activate your subscription."
    )

    st.markdown(
        "[👉 Click Here to Subscribe for $29/mo](https://buy.stripe.com/your_test_link)",
        unsafe_allow_html=True,
    )

    user_key = st.text_input(
        "Enter your license key to unlock download:",
        type="password",
        key=f"{unlock_key}_input",
    )

    if user_key and is_key_valid(user_key):
        st.session_state[unlock_key] = True
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Download Full CSV",
            data=csv_bytes,
            file_name=filename,
            mime="text/csv",
            key=f"{unlock_key}_download",
        )
    elif user_key != "":
        st.session_state[unlock_key] = False
        st.error("Invalid or inactive license key. Please subscribe to get access.")


# ==========================================================================
# TOOL 1: BULK RESUME PARSER
# ==========================================================================
RESUME_SYSTEM_PROMPT = (
    "You are a data extraction bot. Read the resume text. Extract the "
    "candidate's Full Name, Email, Phone Number, and Top 3 Skills. "
    "Respond ONLY with valid JSON in this format: "
    '{"name": "", "email": "", "phone": "", "skills": ""}'
)


def extract_text_from_pdf(uploaded_file) -> str:
    """Extract raw text from an uploaded PDF file using pdfplumber."""
    text_chunks = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_chunks.append(page_text)
    return "\n".join(text_chunks)


def call_openai_for_resume(resume_text: str) -> dict | None:
    """
    Sends resume text to OpenAI and attempts to parse the structured
    JSON response. Returns None (and logs a warning) on any failure so
    that a single bad resume doesn't crash the whole batch.
    """
    if client is None:
        return None

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": RESUME_SYSTEM_PROMPT},
                {"role": "user", "content": resume_text[:12000]},  # guard against huge inputs
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw_content = response.choices[0].message.content
        parsed = json.loads(raw_content)

        # Normalize expected keys so downstream DataFrame columns are consistent
        return {
            "name": parsed.get("name", ""),
            "email": parsed.get("email", ""),
            "phone": parsed.get("phone", ""),
            "skills": parsed.get("skills", ""),
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from OpenAI response: {e}")
        return None
    except Exception as e:
        # Catches API errors, rate limits, network issues, etc.
        logger.error(f"OpenAI API call failed: {e}")
        return None


def render_resume_parser_tool():
    st.header("Bulk Resume Parser")
    st.caption(
        "Upload up to 20 PDF resumes. We extract Name, Email, Phone, and "
        "Skills into a clean spreadsheet."
    )

    if client is None:
        st.error(
            "⚠️ OPENAI_API_KEY environment variable is not set. "
            "Please configure it before using this tool."
        )

    uploaded_files = st.file_uploader(
        "Upload resumes (PDF)",
        type=["pdf"],
        accept_multiple_files=True,
        help="Select up to 20 PDF files.",
    )

    if uploaded_files and len(uploaded_files) > 20:
        st.error("Please upload no more than 20 resumes at a time.")
        uploaded_files = uploaded_files[:20]

    process_clicked = st.button("Process Resumes", type="primary", disabled=(client is None))

    if process_clicked:
        if not uploaded_files:
            st.error("Please upload at least one PDF resume first.")
        else:
            results = []
            errors = []
            progress_bar = st.progress(0.0, text="Starting...")
            total = len(uploaded_files)

            for idx, pdf_file in enumerate(uploaded_files, start=1):
                progress_bar.progress(
                    idx / total, text=f"Processing {pdf_file.name} ({idx}/{total})..."
                )
                try:
                    raw_text = extract_text_from_pdf(pdf_file)
                    if not raw_text.strip():
                        errors.append(f"{pdf_file.name}: no extractable text (possibly scanned/image PDF).")
                        continue

                    extracted = call_openai_for_resume(raw_text)
                    if extracted is None:
                        errors.append(f"{pdf_file.name}: failed to extract structured data.")
                        continue

                    extracted["source_file"] = pdf_file.name
                    results.append(extracted)

                except Exception as e:
                    logger.error(f"Unexpected error processing {pdf_file.name}: {e}")
                    errors.append(f"{pdf_file.name}: unexpected error ({e}).")

            progress_bar.empty()

            if results:
                df = pd.DataFrame(results, columns=["name", "email", "phone", "skills", "source_file"])
                st.session_state["resume_df"] = df
                st.success(f"Processed {len(results)} resumes successfully.")
            else:
                st.session_state["resume_df"] = None
                st.error("No resumes could be processed successfully.")

            if errors:
                with st.expander(f"⚠️ {len(errors)} file(s) had issues"):
                    for err in errors:
                        st.write(f"- {err}")

    # Paywall / download section
    if st.session_state["resume_df"] is not None:
        render_paywall_section(
            st.session_state["resume_df"],
            unlock_key="resume_paid_unlock",
            filename="parsed_resumes.csv",
        )


# ==========================================================================
# TOOL 2: E-COMMERCE CSV MAPPER
# ==========================================================================
SHOPIFY_FIELD_LABELS = {
    "title": "Map to 'Title':",
    "body_html": "Map to 'Body HTML (Description)':",
    "variant_price": "Map to 'Variant Price (Cost)':",
    "image_src": "Map to 'Image Src (URL)':",
    "vendor": "Map to 'Vendor':",
    "variant_sku": "Map to 'Variant SKU':",
}


def render_ecommerce_mapper_tool():
    st.header("Supplier CSV to Shopify Mapper")
    st.caption(
        "Upload your supplier's messy CSV. Map columns, apply a profit "
        "markup, and get a Shopify-ready file."
    )

    uploaded_csv = st.file_uploader("Upload supplier CSV", type=["csv"])

    if uploaded_csv is not None:
        try:
            supplier_df = pd.read_csv(uploaded_csv)
        except Exception as e:
            st.error(f"Could not read this CSV file: {e}")
            return

        if supplier_df.empty:
            st.error("The uploaded CSV appears to be empty.")
            return

        columns = list(supplier_df.columns)

        st.divider()
        st.subheader("Configuration")

        col_a, col_b = st.columns(2)
        with col_a:
            markup_multiplier = st.text_input(
                "Profit Markup Multiplier (e.g., 1.4 for 40% margin):",
                value="1.4",
            )
        with col_b:
            static_qty = st.text_input(
                "Static Inventory Quantity (e.g., 100):",
                value="100",
            )

        st.subheader("Column Mapping")
        map_col1, map_col2 = st.columns(2)

        with map_col1:
            title_map = st.selectbox(SHOPIFY_FIELD_LABELS["title"], columns, key="map_title")
            body_html_map = st.selectbox(SHOPIFY_FIELD_LABELS["body_html"], columns, key="map_body_html")
            price_map = st.selectbox(SHOPIFY_FIELD_LABELS["variant_price"], columns, key="map_price")

        with map_col2:
            image_map = st.selectbox(SHOPIFY_FIELD_LABELS["image_src"], columns, key="map_image")
            vendor_map = st.selectbox(SHOPIFY_FIELD_LABELS["vendor"], columns, key="map_vendor")
            sku_map = st.selectbox(SHOPIFY_FIELD_LABELS["variant_sku"], columns, key="map_sku")

        process_clicked = st.button("Process & Generate CSV", type="primary")

        if process_clicked:
            # --- Validate numeric inputs gracefully ---
            try:
                multiplier_val = float(markup_multiplier)
            except ValueError:
                st.error("Markup Multiplier must be a valid number (e.g., 1.4).")
                return

            try:
                qty_val = int(static_qty)
            except ValueError:
                st.error("Static Inventory Quantity must be a valid whole number (e.g., 100).")
                return

            try:
                # --- Build the Shopify-ready DataFrame ---
                shopify_df = pd.DataFrame()
                shopify_df["Title"] = supplier_df[title_map]
                shopify_df["Body HTML"] = supplier_df[body_html_map]

                # Coerce price column to numeric, invalid entries become NaN
                cost_series = pd.to_numeric(supplier_df[price_map], errors="coerce")
                shopify_df["Variant Price"] = (cost_series * multiplier_val).round(2)

                shopify_df["Image Src"] = supplier_df[image_map]
                shopify_df["Vendor"] = supplier_df[vendor_map]
                shopify_df["Variant SKU"] = supplier_df[sku_map]
                shopify_df["Variant Inventory Qty"] = qty_val

                # Flag rows where price couldn't be computed, but don't drop them
                missing_price_count = shopify_df["Variant Price"].isna().sum()

                st.session_state["shopify_df"] = shopify_df
                st.success("CSV mapped successfully.")

                if missing_price_count > 0:
                    st.warning(
                        f"⚠️ {missing_price_count} row(s) had a non-numeric cost value "
                        "and were left blank in 'Variant Price'."
                    )

            except Exception as e:
                logger.error(f"Error mapping CSV: {e}")
                st.error(f"Something went wrong while mapping your CSV: {e}")

    # Paywall / download section
    if st.session_state["shopify_df"] is not None:
        render_paywall_section(
            st.session_state["shopify_df"],
            unlock_key="shopify_paid_unlock",
            filename="shopify_import.csv",
        )


# ==========================================================================
# SIDEBAR / TOOL SELECTOR
# ==========================================================================
def main():
    st.sidebar.title("🧭 Data Funnel Engine")
    st.sidebar.caption("One engine. Two funnels.")

    tool_choice = st.sidebar.radio(
        "Select Tool:",
        ["Resume Parser", "E-Commerce CSV Mapper"],
    )

    st.sidebar.divider()
    if client is None:
        st.sidebar.error("OPENAI_API_KEY not set — Resume Parser is disabled.")
    else:
        st.sidebar.success("OpenAI connection configured ✅")

    st.title("Data Funnel Engine")

    if tool_choice == "Resume Parser":
        render_resume_parser_tool()
    else:
        render_ecommerce_mapper_tool()


if __name__ == "__main__":
    main()
