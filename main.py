"""Quantum Logistics API: FastAPI + Stripe Checkout + durable SQLite storage.

Production notes:
- Configure STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_ID and PUBLIC_BASE_URL.
- Use Stripe TEST keys and a test Price while validating.
- Mount a persistent Render disk at /var/data or set DATABASE_PATH to a persistent path.
- For multi-instance production deployments, replace SQLite with managed PostgreSQL.
- This sample stores API keys in SQLite so they can be delivered after payment. Protect the
  database file and backups; for higher assurance, store encrypted secrets or use a vault.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
from contextlib import contextmanager
from typing import Any

import stripe
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Security, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("quantum-logistics")

APP_NAME = "Quantum Logistics API"
DATABASE_PATH = os.getenv("DATABASE_PATH", "/var/data/quantum_logistics.db")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
API_KEY_HEADER_NAME = "access_token"

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI(
    title=APP_NAME,
    version="2.0.0",
    description="API de logística con checkout de Stripe y activación verificada por webhook.",
)
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


def _db_path() -> str:
    # Make local development work when /var/data is not available.
    parent = os.path.dirname(DATABASE_PATH)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            return "quantum_logistics.db"
    return DATABASE_PATH


@contextmanager
def db_connection():
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                api_key TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                stripe_customer_id TEXT,
                checkout_session_id TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


@app.on_event("startup")
def startup() -> None:
    init_db()


class CheckoutRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    # Price and amount are intentionally not accepted from the client.
    # They are selected server-side via STRIPE_PRICE_ID.


class DestinationsRequest(BaseModel):
    destinations: list[str] = Field(min_length=2, max_length=500)


def require_stripe_config() -> None:
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID or not PUBLIC_BASE_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe no está configurado. Verifique STRIPE_SECRET_KEY, STRIPE_PRICE_ID y PUBLIC_BASE_URL.",
        )


def get_api_key(api_key: str | None = Security(api_key_header)) -> str:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el encabezado access_token.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    with db_connection() as conn:
        row = conn.execute(
            "SELECT api_key FROM api_keys WHERE api_key = ? AND active = 1",
            (api_key,),
        ).fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clave de API inválida o inactiva. Complete una compra válida o contacte al administrador.",
        )
    return api_key


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "online", "service": APP_NAME, "version": app.version}


@app.get("/health")
def health() -> dict[str, str]:
    # Do not expose secrets or detailed configuration.
    try:
        with db_connection() as conn:
            conn.execute("SELECT 1")
        db_status = "ok"
    except sqlite3.Error:
        db_status = "error"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "stripe_configured": str(bool(STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET and STRIPE_PRICE_ID)).lower(),
    }


@app.post("/create-checkout-session")
def create_checkout_session(payload: CheckoutRequest) -> dict[str, str]:
    require_stripe_config()
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            customer_email=payload.email,
            client_reference_id=payload.email.lower().strip(),
            metadata={"service": "quantum-logistics-api"},
            success_url=f"{PUBLIC_BASE_URL}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{PUBLIC_BASE_URL}/payment/cancel",
        )
    except stripe.error.StripeError as exc:
        logger.exception("Stripe Checkout session creation failed")
        raise HTTPException(status_code=502, detail="No se pudo crear la sesión de pago en Stripe.") from exc
    if not session.url:
        raise HTTPException(status_code=502, detail="Stripe no devolvió una URL de Checkout.")
    return {"checkout_url": session.url, "session_id": session.id}


@app.get("/payment/success")
def payment_success(session_id: str) -> dict[str, str]:
    # The return URL is not proof of payment; fulfillment is performed by the webhook.
    return {
        "status": "received",
        "message": "Regreso de Checkout recibido. La activación depende de la confirmación del webhook de Stripe.",
        "session_id": session_id,
    }


@app.get("/payment/cancel")
def payment_cancel() -> dict[str, str]:
    return {"status": "cancelled", "message": "El pago no se ha confirmado. Puede volver a intentarlo."}


def _activate_paid_session(session: Any) -> None:
    """Persist activation only for a paid, expected Checkout Session."""
    if session.get("mode") != "payment":
        logger.warning("Ignoring non-payment Checkout Session %s", session.get("id"))
        return
    if session.get("payment_status") != "paid":
        logger.info("Checkout Session %s not paid; no API key activated", session.get("id"))
        return
    if session.get("currency") != os.getenv("EXPECTED_CURRENCY", "usd").lower():
        logger.error("Unexpected currency for Checkout Session %s", session.get("id"))
        return
    if not session.get("id") or not session.get("customer_email"):
        logger.error("Paid Checkout Session lacks session ID or customer email")
        return

    # Confirm the session contains the configured Price, rather than trusting client metadata.
    line_items = stripe.checkout.Session.list_line_items(session["id"], limit=10)
    expected = STRIPE_PRICE_ID
    if not expected or not any(
        (item.get("price") or {}).get("id") == expected for item in line_items.data
    ):
        logger.error("Checkout Session %s did not contain the configured Price", session["id"])
        return

    with db_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM api_keys WHERE checkout_session_id = ?", (session["id"],)
        ).fetchone()
        if existing:
            return
        key = "ql_live_" + secrets.token_urlsafe(32)
        conn.execute(
            """INSERT INTO api_keys(email, api_key, active, stripe_customer_id, checkout_session_id)
               VALUES (?, ?, 1, ?, ?)""",
            (
                session["customer_email"].strip().lower(),
                key,
                session.get("customer"),
                session["id"],
            ),
        )
    # Do not log API keys. Configure email delivery before enabling paid production access.
    logger.info("API key activated for customer email %s (session %s)", session["customer_email"], session["id"])
    logger.warning(
        "API key created but not automatically delivered. Add a secure customer portal/email delivery workflow."
    )


@app.post("/webhook/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, str]:
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook de Stripe no configurado.")
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Falta la firma de Stripe.")

    body = await request.body()
    try:
        event = stripe.Webhook.construct_event(body, stripe_signature, STRIPE_WEBHOOK_SECRET)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Payload inválido.") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Firma de Stripe inválida.") from exc

    event_id = event.get("id")
    event_type = event.get("type")
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Evento de Stripe incompleto.")

    with db_connection() as conn:
        already_processed = conn.execute(
            "SELECT event_id FROM processed_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if already_processed:
            return {"status": "already_processed"}

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event["data"]["object"]
        # For async methods, retrieve the latest session from Stripe before granting access.
        try:
            latest = stripe.checkout.Session.retrieve(session["id"])
            _activate_paid_session(latest)
        except stripe.error.StripeError as exc:
            logger.exception("Could not verify Checkout Session")
            raise HTTPException(status_code=502, detail="No se pudo verificar la sesión con Stripe.") from exc
    elif event_type == "checkout.session.async_payment_failed":
        logger.info("Asynchronous payment failed for session %s", event["data"]["object"].get("id"))
    else:
        logger.debug("Ignoring Stripe event type %s", event_type)

    with db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_events(event_id, event_type) VALUES (?, ?)",
            (event_id, event_type),
        )
    return {"status": "success"}


@app.post("/optimize-route")
def optimize_route(
    payload: DestinationsRequest,
    api_key: str = Depends(get_api_key),
) -> dict[str, Any]:
    """Deterministic baseline heuristic; not a quantum solver or a measured fuel-savings claim."""
    destinations = [item.strip() for item in payload.destinations]
    if any(not item for item in destinations):
        raise HTTPException(status_code=422, detail="Los destinos no pueden estar vacíos.")
    if len(set(destinations)) != len(destinations):
        raise HTTPException(status_code=422, detail="La lista contiene destinos duplicados.")
    route = sorted(destinations, key=str.casefold)
    return {
        "status": "success",
        "algorithm": "alphabetical_baseline_heuristic",
        "total_stops": len(route),
        "optimized_route": route,
        "estimated_fuel_savings_percent": None,
        "note": "No se estiman ahorros sin coordenadas, matriz de distancias y comparación de referencia.",
    }


@app.get("/admin/api-keys")
def list_api_keys(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or not x_admin_token or not secrets.compare_digest(x_admin_token, admin_token):
        raise HTTPException(status_code=403, detail="No autorizado.")
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT id, email, active, stripe_customer_id, checkout_session_id, created_at FROM api_keys ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}
