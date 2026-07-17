"""
DataMorph
=========
A Micro-SaaS Streamlit app with 3 e-commerce tools:
  1. Product Description Generator - generates SEO-optimized descriptions
     from product data using AI.
  2. SEO Title Optimizer - transforms basic product titles into
     keyword-rich optimized titles.
  3. E-Commerce CSV Mapper - maps a messy supplier CSV to a Shopify-ready
     product import file, with markup pricing.

------------------------------------------------------------------------
INSTALLATION
------------------------------------------------------------------------
    pip install streamlit pandas openai pdfplumber

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

from license_db import is_key_valid, get_key_info, mark_key_used

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
OPENAI_API_KEY = os.environ.get("GROQ_API_KEY")

client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY, base_url="https://api.groq.com/openai/v1")
else:
    logger.warning("GROQ_API_KEY environment variable is not set.")


# ==========================================================================
# SESSION STATE INITIALIZATION
# ==========================================================================
# We use session_state so that processed data survives Streamlit re-runs
# (e.g. when the user toggles a checkbox or clicks a different button).
def init_session_state():
    defaults = {
        "description_df": None,
        "seo_df": None,
        "shopify_df": None,
        "description_paid_unlock": False,
        "seo_paid_unlock": False,
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
    """
    st.subheader("Preview Processed Data")
    st.dataframe(df.head(5), use_container_width=True)

    st.warning(
        "Your file is ready! Choose a plan to download the full dataset."
    )

    col1, col2 = st.columns(2)
    with col1:
        st.info("**One-Time Access — $7**\n\nPay once, process one file.")
        st.markdown(
            "[Pay $7 (One-Time)](https://buy.stripe.com/28E7sF0aj6bocDkama3sI02)",
            unsafe_allow_html=True,
        )
    with col2:
        st.info("**Monthly Subscription — $29/mo**\n\nUnlimited processing, cancel anytime.")
        st.markdown(
            "[Subscribe $29/mo](https://buy.stripe.com/cNi00dcX5eHU1YG9i63sI03)",
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
        download = st.download_button(
            label="Download Full CSV",
            data=csv_bytes,
            file_name=filename,
            mime="text/csv",
            key=f"{unlock_key}_download",
        )
        if download:
            key_info = get_key_info(user_key)
            if key_info and key_info.get("type") == "one_time":
                mark_key_used(user_key)
                st.info("This was a one-time key. It has been used and is no longer valid.")
    elif user_key != "":
        st.session_state[unlock_key] = False
        st.error("Invalid or inactive license key. Please purchase or subscribe to get access.")


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


def call_description_generator(product_info: str) -> dict | None:
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": DESCRIPTION_SYSTEM_PROMPT},
                {"role": "user", "content": product_info[:4000]},
            ],
            temperature=0.7,
        )
        raw_content = response.choices[0].message.content.strip()
        if raw_content.startswith("```"):
            raw_content = raw_content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw_content)
    except Exception as e:
        logger.error(f"Description generation failed: {e}")
        return None


def call_seo_title_optimizer(title: str) -> dict | None:
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SEO_TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Product title: {title}"},
            ],
            temperature=0.7,
        )
        raw_content = response.choices[0].message.content.strip()
        if raw_content.startswith("```"):
            raw_content = raw_content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw_content)
    except Exception as e:
        logger.error(f"SEO title optimization failed: {e}")
        return None


# ==========================================================================
# TOOL 1: PRODUCT DESCRIPTION GENERATOR
# ==========================================================================
DESCRIPTION_SYSTEM_PROMPT = (
    "You are an expert e-commerce copywriter. Read the product information below. "
    "Generate a unique, SEO-optimized product description (150-300 words) that: "
    "- Highlights key features and benefits "
    "- Uses relevant keywords naturally "
    "- Has a compelling, conversion-focused tone "
    "- Is structured with short paragraphs for readability "
    "Respond ONLY with valid JSON: "
    '{"description": "..."}'
)


def render_description_generator_tool():
    st.header("Product Description Generator")
    st.caption(
        "Upload a CSV with product names and features. Our AI generates "
        "unique, SEO-optimized descriptions for each product."
    )

    if client is None:
        st.error("⚠️ GROQ_API_KEY not set. Please configure it before using this tool.")

    uploaded_file = st.file_uploader("Upload product data (CSV)", type=["csv"])

    if uploaded_file:
        try:
            product_df = pd.read_csv(uploaded_file)
            st.dataframe(product_df.head(10), use_container_width=True)
            st.info(f"Loaded {len(product_df)} products.")
            name_col = st.selectbox("Select product name column:", product_df.columns, key="desc_name")
            feature_col = st.selectbox("Select features column:", product_df.columns, key="desc_features")

            process_clicked = st.button("Generate Descriptions", type="primary", disabled=(client is None))
            if process_clicked:
                results, errors = [], []
                progress_bar = st.progress(0.0, text="Starting...")
                total = len(product_df)
                for idx, row in product_df.iterrows():
                    progress_bar.progress((idx + 1) / total, text=f"Processing {idx + 1}/{total}...")
                    product_info = f"Product: {row[name_col]}\nFeatures: {row[feature_col]}"
                    parsed = call_description_generator(product_info)
                    if parsed:
                        results.append({"product_name": row[name_col], "features": row[feature_col], "generated_description": parsed.get("description", "")})
                    else:
                        errors.append(f"Product {idx + 1}: generation failed")
                progress_bar.empty()
                if results:
                    desc_df = pd.DataFrame(results)
                    st.session_state["description_df"] = desc_df
                    st.success(f"Generated descriptions for {len(results)} products.")
                    st.dataframe(desc_df, use_container_width=True)
                else:
                    st.session_state["description_df"] = None
                    st.error("No descriptions could be generated.")
                if errors:
                    with st.expander(f"⚠️ {len(errors)} product(s) had issues"):
                        for err in errors:
                            st.write(f"- {err}")
        except Exception as e:
            st.error(f"Error reading CSV: {e}")

    if st.session_state.get("description_df") is not None:
        render_paywall_section(st.session_state["description_df"], unlock_key="description_paid_unlock", filename="product_descriptions.csv")


# ==========================================================================
# TOOL 2: SEO TITLE OPTIMIZER
# ==========================================================================
SEO_TITLE_SYSTEM_PROMPT = (
    "You are an SEO expert for e-commerce. Read the product title below. "
    "Generate 3 optimized title variations that: "
    "- Include high-ranking keywords "
    "- Stay under 70 characters (for Google) "
    "- Are compelling and click-worthy "
    "Respond ONLY with valid JSON: "
    '{"title_1": "...", "title_2": "...", "title_3": "..."}'
)


def render_seo_title_tool():
    st.header("SEO Title Optimizer")
    st.caption(
        "Upload a CSV with product titles. Our AI generates optimized, "
        "keyword-rich title variations for better search rankings."
    )

    if client is None:
        st.error("⚠️ GROQ_API_KEY not set. Please configure it before using this tool.")

    uploaded_file = st.file_uploader("Upload product titles (CSV)", type=["csv"])

    if uploaded_file:
        try:
            title_df = pd.read_csv(uploaded_file)
            st.dataframe(title_df.head(10), use_container_width=True)
            st.info(f"Loaded {len(title_df)} products.")
            title_col = st.selectbox("Select title column:", title_df.columns, key="seo_title")

            process_clicked = st.button("Optimize Titles", type="primary", disabled=(client is None))
            if process_clicked:
                results, errors = [], []
                progress_bar = st.progress(0.0, text="Starting...")
                total = len(title_df)
                for idx, row in title_df.iterrows():
                    progress_bar.progress((idx + 1) / total, text=f"Optimizing {idx + 1}/{total}...")
                    parsed = call_seo_title_optimizer(str(row[title_col]))
                    if parsed:
                        results.append({"original_title": row[title_col], "optimized_title_1": parsed.get("title_1", ""), "optimized_title_2": parsed.get("title_2", ""), "optimized_title_3": parsed.get("title_3", "")})
                    else:
                        errors.append(f"Title {idx + 1}: optimization failed")
                progress_bar.empty()
                if results:
                    seo_df = pd.DataFrame(results)
                    st.session_state["seo_df"] = seo_df
                    st.success(f"Optimized {len(results)} titles.")
                    st.dataframe(seo_df, use_container_width=True)
                else:
                    st.session_state["seo_df"] = None
                    st.error("No titles could be optimized.")
                if errors:
                    with st.expander(f"⚠️ {len(errors)} title(s) had issues"):
                        for err in errors:
                            st.write(f"- {err}")
        except Exception as e:
            st.error(f"Error reading CSV: {e}")

    if st.session_state.get("seo_df") is not None:
        render_paywall_section(st.session_state["seo_df"], unlock_key="seo_paid_unlock", filename="optimized_titles.csv")


# ==========================================================================
# TOOL 3: E-COMMERCE CSV MAPPER
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
    st.sidebar.title("🧭 DataMorph")
    st.sidebar.caption("3 AI tools for e-commerce store owners.")

    tool_choice = st.sidebar.radio(
        "Select Tool:",
        ["Product Description Generator", "SEO Title Optimizer", "E-Commerce CSV Mapper"],
    )

    st.sidebar.divider()
    if client is None:
        st.sidebar.error("GROQ_API_KEY not set — AI tools are disabled.")
    else:
        st.sidebar.success("Groq AI connection configured ✅")

    st.title("DataMorph")

    if tool_choice == "Product Description Generator":
        render_description_generator_tool()
    elif tool_choice == "SEO Title Optimizer":
        render_seo_title_tool()
    else:
        render_ecommerce_mapper_tool()


if __name__ == "__main__":
    main()
