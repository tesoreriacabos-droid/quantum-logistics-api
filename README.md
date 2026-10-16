# Quantum Logistics API — corrected baseline

## Corrections included
- Imports `status` and removes the previous `ACTIVE_API_KEYS` in-memory-only storage.
- Uses SQLite persistence and a unique Checkout Session constraint for activation idempotency.
- Creates Stripe Checkout sessions server-side from `STRIPE_PRICE_ID`; the client cannot choose a price.
- Verifies webhook signatures and retrieves the current Checkout Session from Stripe.
- Activates keys only when `payment_status == "paid"`, mode is `payment`, currency matches `EXPECTED_CURRENCY`, and the configured Price is present.
- Handles completed, async success, and async failure Checkout events.
- Removes the unsupported fixed 18.5% fuel-savings claim.
- Adds `/health`, `/payment/success`, `/payment/cancel`, and a token-protected key metadata endpoint.

## Render setup
1. Push these files to the GitHub repository (or copy them into a separate branch first).
2. In Render, set the build command to `pip install -r requirements.txt` and start command to `uvicorn main:app --host 0.0.0.0 --port $PORT`.
3. Mount a persistent disk at `/var/data` and set `DATABASE_PATH=/var/data/quantum_logistics.db`. `render.yaml` describes this setup; confirm your plan supports persistent disks.
4. Set environment variables in Render:
   - `STRIPE_SECRET_KEY`: Stripe test secret key for testing.
   - `STRIPE_WEBHOOK_SECRET`: signing secret for the webhook endpoint.
   - `STRIPE_PRICE_ID`: a Price ID (`price_...`), not a Product ID (`prod_...`).
   - `PUBLIC_BASE_URL`: exact public service URL, e.g. `https://your-service.onrender.com`.
   - `ADMIN_TOKEN`: long random secret.
   - `EXPECTED_CURRENCY`: currency of the Price, default `usd`.
5. In Stripe **test mode**, add webhook endpoint `https://YOUR-SERVICE.onrender.com/webhook/stripe` with events:
   - `checkout.session.completed`
   - `checkout.session.async_payment_succeeded`
   - `checkout.session.async_payment_failed`
6. Copy that endpoint's signing secret (`whsec_...`) into Render. It is different from the Stripe API secret.
7. Deploy and check `GET /health`.

## Create a test checkout
```bash
curl -X POST "https://YOUR-SERVICE.onrender.com/create-checkout-session" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com"}'
```
Open the returned `checkout_url` and use a Stripe test card such as `4242 4242 4242 4242`, a future expiry, any CVC, and a test postal code. Use only in Stripe test mode.

## Call the route endpoint
```bash
curl -X POST "https://YOUR-SERVICE.onrender.com/optimize-route" \
  -H "Content-Type: application/json" \
  -H "access_token: YOUR_API_KEY" \
  -d '{"destinations":["Bodega","Centro","Aeropuerto"]}'
```

## Important limitations before taking real money
- This is a corrected integration baseline, not a verified deployment. I cannot push commits to your GitHub repository or change Render/Stripe settings from this package.
- **Customer key delivery is not implemented.** The key is persisted after a verified payment, but is intentionally never logged or returned by the webhook. Add a secure email/portal delivery flow before selling access; otherwise the customer cannot retrieve their key.
- SQLite on a persistent disk is suitable for a single service instance, not horizontal scaling. Use managed PostgreSQL for multi-instance production.
- The route function is a simple alphabetical baseline, not a real route optimizer. A real optimizer needs coordinates, distance/time matrices, constraints, and validation against a baseline.
- Have a security review before production. Do not store or commit `.env`, API secrets, webhook secrets, or customer data in Git.
