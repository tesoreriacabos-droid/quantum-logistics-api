import os
import sqlite3
from datetime import datetime, timezone
from typing import Literal

import stripe
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

APP_NAME = "Quantum Logistics API"
BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://quantum-logistics-api-2.onrender.com",
).rstrip("/")
EXPECTED_CURRENCY = os.getenv("EXPECTED_CURRENCY", "usd").lower()
CHECKOUT_MODE = os.getenv("STRIPE_CHECKOUT_MODE", "payment").lower()
DATABASE_PATH = os.getenv("DATABASE_PATH", "/tmp/quantum_logistics.db")

app = FastAPI(
    title=APP_NAME,
    version="1.1.0",
    description="API de Quantum Logistics con integración de Stripe en modo de prueba.",
)


class CheckoutRequest(BaseModel):
    customer_email: str = Field(min_length=3, max_length=254)


def stripe_config():
    secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    price_id = os.getenv("STRIPE_PRICE_ID", "").strip()
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    return secret_key, price_id, webhook_secret


def require_test_stripe():
    secret_key, price_id, _ = stripe_config()
    if not secret_key or not secret_key.startswith("sk_test_"):
        raise HTTPException(
            status_code=503,
            detail="Stripe no está configurado con una clave secreta de prueba (sk_test_).",
        )
    if not price_id.startswith("price_"):
        raise HTTPException(
            status_code=503,
            detail="Configura STRIPE_PRICE_ID con un identificador price_ válido.",
        )
    if CHECKOUT_MODE not in {"payment", "subscription"}:
        raise HTTPException(
            status_code=500,
            detail="STRIPE_CHECKOUT_MODE debe ser payment o subscription.",
        )
    stripe.api_key = secret_key
    return price_id


def connect_db():
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with connect_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stripe_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS checkout_records (
                session_id TEXT PRIMARY KEY,
                customer_email TEXT,
                payment_status TEXT NOT NULL,
                currency TEXT,
                amount_total INTEGER,
                recorded_at TEXT NOT NULL
            )
            """
        )


init_db()


@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": APP_NAME,
        "message": "API disponible. Consulta /docs para los endpoints.",
    }


@app.get("/health")
def health():
    secret_key, price_id, webhook_secret = stripe_config()
    return {
        "status": "ok",
        "stripe_test_key_configured": secret_key.startswith("sk_test_"),
        "stripe_price_configured": price_id.startswith("price_"),
        "stripe_webhook_secret_configured": webhook_secret.startswith("whsec_"),
        "checkout_mode": CHECKOUT_MODE,
        "currency_expected": EXPECTED_CURRENCY,
    }


@app.post("/create-checkout-session")
def create_checkout_session(data: CheckoutRequest):
    price_id = require_test_stripe()
    email = data.customer_email.strip()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="customer_email no parece válido.")

    try:
        price = stripe.Price.retrieve(price_id)
        if not price.get("active", False):
            raise HTTPException(status_code=400, detail="El precio de Stripe está inactivo.")
        if (price.get("currency") or "").lower() != EXPECTED_CURRENCY:
            raise HTTPException(
                status_code=400,
                detail=f"El precio no coincide con la moneda configurada ({EXPECTED_CURRENCY}).",
            )
        is_recurring = bool(price.get("recurring"))
        if CHECKOUT_MODE == "subscription" and not is_recurring:
            raise HTTPException(
                status_code=400,
                detail="El precio configurado no es recurrente; usa un precio de suscripción.",
            )
        if CHECKOUT_MODE == "payment" and is_recurring:
            raise HTTPException(
                status_code=400,
                detail="El precio configurado es recurrente; usa STRIPE_CHECKOUT_MODE=subscription.",
            )

        session = stripe.checkout.Session.create(
            mode=CHECKOUT_MODE,
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=email,
            success_url=f"{BASE_URL}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/checkout/cancel",
            metadata={"service": "quantum-logistics-api"},
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except HTTPException:
        raise
    except stripe.StripeError:
        raise HTTPException(
            status_code=502,
            detail="Stripe no pudo crear la sesión. Verifica la clave de prueba, el precio y la moneda.",
        )


def validate_paid_session(session, expected_price_id):
    payment_status = session.get("payment_status")
    allowed_statuses = {"paid", "no_payment_required"} if CHECKOUT_MODE == "subscription" else {"paid"}
    if session.get("mode") != CHECKOUT_MODE or payment_status not in allowed_statuses:
        return False

    currency = (session.get("currency") or "").lower()
    if currency and currency != EXPECTED_CURRENCY:
        return False

    try:
        items = stripe.checkout.Session.list_line_items(session.get("id"), limit=10)
        return any(
            (item.get("price") or {}).get("id") == expected_price_id
            for item in items.get("data", [])
        )
    except stripe.StripeError:
        raise HTTPException(status_code=502, detail="No se pudo verificar el detalle del pago en Stripe.")


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    secret_key, price_id, webhook_secret = stripe_config()
    if not secret_key or not secret_key.startswith("sk_test_"):
        raise HTTPException(status_code=503, detail="Stripe no está configurado en modo de prueba.")
    if not webhook_secret.startswith("whsec_"):
        raise HTTPException(status_code=503, detail="Configura STRIPE_WEBHOOK_SECRET en Render.")

    stripe.api_key = secret_key
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, signature, webhook_secret)
    except ValueError:
        raise HTTPException(status_code=400, detail="Cuerpo del webhook no válido.")
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Firma del webhook no válida.")

    event_type = event.get("type", "")
    if event_type not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        return {"status": "ignored", "event_type": event_type}

    if not price_id.startswith("price_"):
        raise HTTPException(status_code=503, detail="Configura STRIPE_PRICE_ID antes de procesar pagos.")

    session = event["data"]["object"]
    if not validate_paid_session(session, price_id):
        # Do not grant access to a session that is unpaid or does not match our configured price.
        with connect_db() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO stripe_events (event_id, event_type, processed_at) VALUES (?, ?, ?)",
                (event["id"], event_type, datetime.now(timezone.utc).isoformat()),
            )
        return {"status": "not_eligible", "access_provisioned": False}

    with connect_db() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO stripe_events (event_id, event_type, processed_at) VALUES (?, ?, ?)",
            (event["id"], event_type, datetime.now(timezone.utc).isoformat()),
        )
        if cursor.rowcount == 0:
            return {"status": "already_processed"}

        connection.execute(
            """
            INSERT OR IGNORE INTO checkout_records
            (session_id, customer_email, payment_status, currency, amount_total, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session.get("id"),
                session.get("customer_details", {}).get("email") or session.get("customer_email"),
                session.get("payment_status", "unknown"),
                (session.get("currency") or "").lower(),
                session.get("amount_total"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    # Payment is verified and recorded. API-key delivery is intentionally not claimed as complete.
    return {"status": "payment_verified", "access_provisioned": False}


@app.get("/checkout/success")
def checkout_success(session_id: str):
    price_id = require_test_stripe()
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if not validate_paid_session(session, price_id):
            raise HTTPException(status_code=402, detail="Stripe no confirma un pago válido.")
        return {
            "status": "payment_verified",
            "session_id": session.id,
            "access_provisioned": False,
            "message": "Pago de prueba confirmado. La entrega automática de credenciales aún requiere configuración.",
        }
    except HTTPException:
        raise
    except stripe.StripeError:
        raise HTTPException(status_code=502, detail="No se pudo consultar la sesión en Stripe.")


@app.get("/checkout/cancel")
def checkout_cancel():
    return {"status": "cancelled", "message": "El checkout fue cancelado; no se confirmó ningún pago."}


@app.post("/optimize-route")
def optimize_route():
    raise HTTPException(
        status_code=501,
        detail="El algoritmo real de optimización y la emisión de credenciales aún no están configurados.",
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
