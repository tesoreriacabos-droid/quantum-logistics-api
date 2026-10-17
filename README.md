# Quantum Logistics API

FastAPI service with Stripe Checkout integration intended for **Stripe test mode**.

## Endpoints
- `GET /`: basic service status.
- `GET /health`: reports whether the required Stripe environment variables appear configured. It does not reveal secret values.
- `POST /create-checkout-session`: creates a Stripe Checkout session using the server-configured `STRIPE_PRICE_ID`; the caller cannot set the amount.
- `POST /webhook/stripe`: verifies Stripe's signature and records eligible payment events idempotently.
- `GET /checkout/success?session_id=...`: checks the Checkout Session against the configured mode and price.
- `GET /checkout/cancel`: cancellation response.
- `POST /optimize-route`: currently returns HTTP 501. A real routing algorithm is not implemented.

## Render environment variables
Set these in Render's environment settings. Never commit actual secrets:
- `STRIPE_SECRET_KEY`: Stripe test secret key beginning `sk_test_`.
- `STRIPE_WEBHOOK_SECRET`: signing secret beginning `whsec_` for the endpoint `/webhook/stripe`.
- `STRIPE_PRICE_ID`: an active Stripe Price ID beginning `price_`, not a Product ID.
- `PUBLIC_BASE_URL`: exact public URL of this service.
- `EXPECTED_CURRENCY`: currency code for the configured price; defaults to `usd`.
- `STRIPE_CHECKOUT_MODE`: `payment` (default) for one-time prices or `subscription` for recurring prices.
- `DATABASE_PATH`: SQLite path; defaults to `/tmp/quantum_logistics.db`.

Configure a webhook endpoint in Stripe **test mode** at `https://quantum-logistics-api-2.onrender.com/webhook/stripe` for:
- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`

Copy the endpoint signing secret into Render's `STRIPE_WEBHOOK_SECRET`. Do not share it in chat or commit it to Git.

## Create a test checkout
Send a JSON request with an email address:
```bash
curl -X POST "https://quantum-logistics-api-2.onrender.com/create-checkout-session" \
  -H "Content-Type: application/json" \
  -d '{"customer_email":"test@example.com"}'
```
The response includes `checkout_url` and `session_id`. Open the URL and use a Stripe test card, for example `4242 4242 4242 4242`, with a future expiry date and any CVC. Only use test cards while the Stripe account is in test mode.

## Important limitations
- The application intentionally does not issue or deliver API keys after payment. The webhook records verified payment status only; customer credential provisioning and a secure delivery portal/email workflow still need to be implemented before selling API access.
- SQLite at `/tmp` is ephemeral and may be lost when the Render service restarts or redeploys. For durable records, configure a persistent disk and set `DATABASE_PATH` to its mount path, or migrate to managed PostgreSQL.
- The API does not claim a fixed fuel-saving percentage. Route optimization is not implemented yet.
- A live Render deployment is not proof of a successful Stripe payment. Verify `/health`, a test Checkout session, webhook delivery in Stripe, and matching application logs before treating payment processing as tested.
