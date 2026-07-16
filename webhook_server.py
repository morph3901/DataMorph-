"""
webhook_server.py
==================
A small standalone Flask service that receives Stripe webhook events and
issues a unique license key per paying customer (via license_db.py).

This runs as a SEPARATE process from the Streamlit app (Streamlit can't
receive incoming webhooks itself). Both processes share the same
license_db.py store.

------------------------------------------------------------------------
INSTALLATION
------------------------------------------------------------------------
    pip install flask stripe resend

------------------------------------------------------------------------
SETUP
------------------------------------------------------------------------
1. In your Stripe Dashboard -> Developers -> API keys, copy your secret key:
       export STRIPE_API_KEY="sk_live_..."   (or sk_test_... while testing)

2. In your Stripe Dashboard -> Developers -> Webhooks, add an endpoint
   pointing at:
       https://your-domain.com/webhook/stripe
   Subscribe it to these events at minimum:
       - checkout.session.completed
       - customer.subscription.deleted
       - customer.subscription.updated
   Stripe will show you a "Signing secret" (starts with whsec_...) --
   set it as:
       export STRIPE_WEBHOOK_SECRET="whsec_..."

3. Email delivery via Resend (https://resend.com) -- free tier covers
   3,000 emails/month:
       export RESEND_API_KEY="re_..."
       export RESEND_FROM_EMAIL="onboarding@resend.dev"   # or your verified domain
   Also update APP_URL below to your real Streamlit app URL once deployed.

------------------------------------------------------------------------
RUN (locally, for testing with `stripe listen`)
------------------------------------------------------------------------
    stripe listen --forward-to localhost:4242/webhook/stripe
    python webhook_server.py
------------------------------------------------------------------------
RUN (production)
------------------------------------------------------------------------
    gunicorn webhook_server:app -b 0.0.0.0:4242
------------------------------------------------------------------------
"""

import os
import logging

import stripe
import resend
from flask import Flask, request, jsonify

from license_db import (
    create_license_key,
    revoke_keys_for_subscription,
    reactivate_keys_for_subscription,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_server")

app = Flask(__name__)

stripe.api_key = os.environ.get("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
APP_URL = "https://your-streamlit-app-url.com"  # TODO: replace with your real Streamlit app URL

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY
else:
    logger.warning("RESEND_API_KEY is not set -- license key emails will not be sent.")


def send_key_email(to_email: str, license_key: str):
    """
    Email the license key to the customer via Resend
    (https://resend.com). Free tier covers 3,000 emails/month, which is
    plenty for a $29/mo micro-SaaS until you're doing real volume.

    This is deliberately wrapped in a broad try/except: a failed email
    must never take down the webhook. Stripe expects a 200 response
    regardless, and the key is already safely stored in Supabase by the
    time this runs -- so a failed send just means the customer needs to
    be followed up with manually (check the logs / Supabase table for
    their key), not that the sale was lost.
    """
    if not RESEND_API_KEY:
        logger.info(f"[send_key_email] Skipped -- RESEND_API_KEY not set. "
                    f"Key {license_key} for {to_email} was NOT emailed.")
        return

    subject = "Your Data Funnel Engine License Key"
    body = (
        "Hi there,\n\n"
        "Thanks for subscribing to Data Funnel Engine! Your subscription is "
        "now active.\n\n"
        f"Your license key: {license_key}\n\n"
        f"Head to the app and paste this key into the \"Enter your license "
        f"key\" box to unlock your downloads:\n{APP_URL}\n\n"
        "Keep this key handy -- you'll need it every time you want to "
        "download a processed file.\n\n"
        "If you have any questions or run into trouble, just reply to this "
        "email.\n\n"
        "Thanks again,\n"
        "The Data Funnel Engine Team"
    )

    try:
        resend.Emails.send({
            "from": RESEND_FROM_EMAIL,
            "to": to_email,
            "subject": subject,
            "text": body,
        })
        logger.info(f"[send_key_email] Sent license key email to {to_email}.")
    except Exception as e:
        # Never let an email failure crash the webhook or block Stripe's 200.
        logger.error(f"[send_key_email] Failed to send email to {to_email}: {e}")


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    if not STRIPE_WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET is not set -- refusing to process webhook.")
        return jsonify({"error": "server misconfigured"}), 500

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        logger.warning("Invalid payload received on /webhook/stripe.")
        return jsonify({"error": "invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        logger.warning("Invalid Stripe signature on /webhook/stripe.")
        return jsonify({"error": "invalid signature"}), 400

    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed":
        handle_checkout_completed(data_object)
    elif event_type == "customer.subscription.deleted":
        handle_subscription_ended(data_object)
    elif event_type == "customer.subscription.updated":
        handle_subscription_updated(data_object)
    else:
        logger.info(f"Ignoring unhandled event type: {event_type}")

    # Always return 200 quickly so Stripe doesn't retry unnecessarily.
    return jsonify({"received": True}), 200


def handle_checkout_completed(session: dict):
    """
    Fires when a customer successfully completes Stripe Checkout for
    the subscription. We mint them a fresh license key.
    """
    customer_email = (session.get("customer_details") or {}).get("email")
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")

    if not customer_email:
        logger.warning("checkout.session.completed with no customer email -- skipping.")
        return

    license_key = create_license_key(
        email=customer_email,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
    )

    logger.info(f"Issued license key {license_key} to {customer_email} "
                f"(customer={customer_id}, subscription={subscription_id})")

    send_key_email(customer_email, license_key)


def handle_subscription_ended(subscription: dict):
    """Fires on cancellation / final payment failure -- revoke their key(s)."""
    subscription_id = subscription.get("id")
    if subscription_id:
        revoke_keys_for_subscription(subscription_id)
        logger.info(f"Revoked license key(s) for subscription {subscription_id}")


def handle_subscription_updated(subscription: dict):
    """
    Fires on status changes (e.g. past_due, active again after retry).
    We revoke on anything other than 'active'/'trialing', and
    reactivate if it comes back to 'active'.
    """
    subscription_id = subscription.get("id")
    status = subscription.get("status")
    if not subscription_id:
        return

    if status in ("active", "trialing"):
        reactivate_keys_for_subscription(subscription_id)
    else:
        revoke_keys_for_subscription(subscription_id)
    logger.info(f"Subscription {subscription_id} status={status} -> keys updated")


if __name__ == "__main__":
    app.run(port=4242, debug=False)


# --------------------------------------------------------------------------
# NOTES
# --------------------------------------------------------------------------
# - Email delivery is now live via Resend. As a belt-and-suspenders
#   improvement later, you can also configure your Stripe Payment Link /
#   Checkout Session with a custom success URL
#   (e.g. https://yourapp.com/success?session_id={CHECKOUT_SESSION_ID})
#   and build a tiny route that calls stripe.checkout.Session.retrieve()
#   and displays the key directly -- useful as a fallback if an email
#   ever bounces or lands in spam, but not required to launch.
# - Idempotency: Stripe can occasionally redeliver the same event. This
#   simple version will mint a second key on a duplicate
#   checkout.session.completed delivery. For production, consider
#   checking event["id"] against a table of already-processed event IDs
#   before acting.
# - Run this on infrastructure with a persistent disk (see license_db.py
#   NOTES) -- Streamlit Community Cloud is not suitable for this file.
