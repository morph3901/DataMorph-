# Data Funnel Engine — Full Deployment Guide

Your repo, in order:
- `app.py` — Streamlit app (Resume Parser + CSV Mapper + paywall)
- `webhook_server.py` — Flask service that listens to Stripe and issues keys
- `license_db.py` — shared Supabase data layer
- `requirements.txt` — dependencies for both processes

Do NOT include `license_db_sqlite_local_only.py` — that was an earlier
draft before the Supabase migration and is dead code.

Total time: ~45–60 minutes if nothing goes wrong, longer if it's your
first time with any of these tools. Go in order — each step depends on
the one before it.

---

## STEP 1 — Push the code to GitHub

1. Create a new **private** repo on GitHub (private is fine — Streamlit
   Cloud and Render can both deploy from private repos once you connect
   your account).
2. From the folder containing your 4 files:
   ```bash
   git init
   git add app.py webhook_server.py license_db.py requirements.txt
   git commit -m "Initial commit: Data Funnel Engine"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```
3. Double check on GitHub.com that exactly those 4 files show up — no
   `.env` file, no `licenses.db`, no API keys committed anywhere.

---

## STEP 2 — Set up Supabase (the database)

1. Go to **supabase.com** → sign up → **New Project**.
   - Pick any name/region. Set a database password and save it somewhere
     (you won't need it directly, but keep it).
   - Wait ~2 minutes for the project to finish provisioning.
2. Open the **SQL Editor** (left sidebar) → **New query** → paste and run:
   ```sql
   CREATE TABLE license_keys (
       license_key TEXT PRIMARY KEY,
       email TEXT,
       stripe_customer_id TEXT,
       stripe_subscription_id TEXT,
       status TEXT NOT NULL DEFAULT 'active',
       created_at TEXT NOT NULL
   );
   ```
   Click **Run**. You should see "Success. No rows returned."
3. **Check Row Level Security (RLS):** go to **Table Editor** → click
   `license_keys` → look for an RLS toggle/banner at the top.
   - If RLS is **ON** by default in your project and you don't add a
     policy, `app.py`'s anon-key reads will get blocked. Simplest fix
     for now: turn RLS **OFF** for this table (Table Editor →
     `license_keys` → the RLS toggle). You can lock it down properly
     later with real policies once you're live.
4. Go to **Project Settings** (gear icon) → **API**. You'll need two
   pairs of values from this page later:
   - **Project URL** (`https://xxxxx.supabase.co`)
   - **anon / public key** — goes to Streamlit (read-only use)
   - **service_role key** — goes to Render (read/write use). Keep this
     one especially secret — it bypasses RLS entirely.

---

## STEP 3 — Set up Resend (email delivery)

1. Go to **resend.com** → sign up.
2. **API Keys** → **Create API Key**. Copy it immediately (starts with
   `re_...`) — you won't see it again.
3. For testing, you can send from `onboarding@resend.dev` with no extra
   setup. For a real launch, add your own domain under **Domains** and
   verify it (DNS records), then use `you@yourdomain.com` as the sender
   — deliverability is noticeably better than the shared onboarding
   address.

---

## STEP 4 — Set up Stripe

1. In your **Stripe Dashboard**, make sure you're in **Test mode**
   (toggle top-right) while you set everything up.
2. Create your $29/mo subscription product: **Product catalog** →
   **Add product** → set it recurring, $29/month.
3. Create a **Payment Link** for that product (Product page → **Create
   payment link**). Copy the link — this replaces the placeholder
   `https://buy.stripe.com/your_test_link` in `app.py`. Update that
   line in `app.py` and push the change to GitHub before deploying, or
   plan to do it right after Step 5.
4. Get your **secret API key**: **Developers** → **API keys** → copy the
   **Secret key** (`sk_test_...` for now). This goes to Render.
5. You'll set up the **webhook endpoint** itself in Step 6, after Render
   gives you a URL to point it at — Stripe needs that URL to exist first.

---

## STEP 5 — Deploy the Streamlit app (Streamlit Cloud)

1. Go to **share.streamlit.io** → sign in with GitHub → **New app**.
2. Pick your repo, branch `main`, and set the main file path to `app.py`.
3. Before clicking Deploy, open **Advanced settings** → **Secrets**, and
   paste:
   ```toml
   OPENAI_API_KEY = "sk-..."
   SUPABASE_URL = "https://xxxxx.supabase.co"
   SUPABASE_KEY = "your-anon-public-key"
   ```
4. Click **Deploy**. First build takes a few minutes (installing
   `pdfplumber`, `supabase`, etc.).
5. Once it's live, copy the app's URL
   (`https://your-app-name.streamlit.app`) — you'll need it in Step 8.

---

## STEP 6 — Deploy the webhook server (Render)

1. Go to **render.com** → sign in with GitHub → **New** → **Web
   Service** → connect your repo.
2. Configuration:
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn webhook_server:app -b 0.0.0.0:$PORT`
     (Render sets `$PORT` for you — don't hardcode 4242 here)
3. Add environment variables (**Environment** tab):
   ```
   STRIPE_API_KEY = sk_test_...
   STRIPE_WEBHOOK_SECRET = (leave blank for now, filled in Step 7)
   SUPABASE_URL = https://xxxxx.supabase.co
   SUPABASE_KEY = your-service_role-key      <-- service_role, NOT anon
   RESEND_API_KEY = re_...
   RESEND_FROM_EMAIL = onboarding@resend.dev
   ```
4. Deploy. Once live, copy the Render URL, e.g.
   `https://your-app.onrender.com`. Your webhook endpoint is:
   ```
   https://your-app.onrender.com/webhook/stripe
   ```

   Note: Render's free tier spins down after inactivity and takes ~30–60
   seconds to wake up on the next request. For a payment webhook this is
   usually fine (Stripe retries failed deliveries), but if you want zero
   cold-start delay before launch, consider a paid Render instance later.

---

## STEP 7 — Connect Stripe's webhook to Render

1. Back in **Stripe Dashboard** → **Developers** → **Webhooks** → **Add
   endpoint**.
2. Endpoint URL: `https://your-app.onrender.com/webhook/stripe`
3. Select events to listen for:
   - `checkout.session.completed`
   - `customer.subscription.deleted`
   - `customer.subscription.updated`
4. Save. Stripe will show a **Signing secret** (`whsec_...`) — copy it.
5. Go back to **Render** → your service → **Environment** → set:
   ```
   STRIPE_WEBHOOK_SECRET = whsec_...
   ```
   Save — Render will redeploy automatically with the new value.

---

## STEP 8 — Fill in the remaining placeholders

Two placeholders need real values now that both services are live:

1. In `webhook_server.py`, near the top:
   ```python
   APP_URL = "https://your-streamlit-app-url.com"
   ```
   Change to your actual Streamlit URL from Step 5, e.g.
   `https://your-app-name.streamlit.app`.

2. In `app.py`, the Stripe payment link:
   ```python
   st.markdown('[👉 Click Here to Subscribe for $29/mo](https://buy.stripe.com/your_test_link)', ...)
   ```
   Change to your real Payment Link from Step 4.

Commit and push both changes:
```bash
git add app.py webhook_server.py
git commit -m "Fill in real app URL and Stripe payment link"
git push
```
Both Streamlit Cloud and Render auto-redeploy on push to `main`.

---

## STEP 9 — Test the full loop (in Stripe test mode)

1. Open your live Streamlit app, upload a resume or CSV, and confirm the
   preview + paywall UI shows up correctly.
2. Click your Stripe Payment Link, and pay with a **test card**:
   ```
   Card number: 4242 4242 4242 4242
   Expiry: any future date
   CVC: any 3 digits
   ZIP: any 5 digits
   ```
3. Check, in order:
   - **Render logs** (Render dashboard → your service → Logs): you
     should see `Issued license key MORPH-XXXX-XXXX to ...`
   - **Supabase Table Editor** → `license_keys`: a new row should
     appear with status `active`.
   - **Your inbox** (the email you used at checkout): you should receive
     the key from Resend within a few seconds. Check spam if using the
     shared `onboarding@resend.dev` sender.
   - **Streamlit app**: paste the key into the "Enter your license key"
     box → the download button should appear.
4. Also test the failure paths:
   - Type a wrong/random key → should show "Invalid or inactive license
     key."
   - In Stripe, cancel the test subscription → within a minute or two,
     confirm the corresponding row in Supabase flips to `status =
     revoked`, and that the same key no longer unlocks downloads in the
     app.

If any step fails, check in this order: Render logs (did the webhook
fire and process without error?) → Supabase table (did the row get
written?) → Resend dashboard's "Emails" tab (did the send succeed or
bounce?).

---

## STEP 10 — Go live

1. In Stripe, toggle from **Test mode** to **Live mode** (top-right).
2. Recreate the Product + Payment Link in live mode (test-mode products
   don't carry over).
3. Get your **live** secret key (`sk_live_...`) and update
   `STRIPE_API_KEY` on Render.
4. Add a **new** webhook endpoint in live mode pointing at the same
   Render URL, and update `STRIPE_WEBHOOK_SECRET` on Render with the
   live signing secret (test and live mode have separate webhook
   secrets, even for the same URL).
5. Update the Stripe Payment Link in `app.py` to the live one, push.
6. Do one real test purchase yourself (refund it after) to confirm the
   full loop works with real money before sending it to anyone else.

At that point the whole loop — payment → key issuance → email → unlock —
runs unattended. You're ready to start driving traffic to it.
