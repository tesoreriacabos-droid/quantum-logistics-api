import os
from fastapi import FastAPI, Request, HTTPException, status
import stripe

app = FastAPI(title="Quantum Logistics API", version="1.0.0")

# Configurar Stripe con la llave secreta desde las variables de entorno de Render
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

@app.get("/")
def read_root():
    return {"status": "online", "message": "Quantum Logistics Optimization API is running."}

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        # Verificar la firma del webhook de Stripe por seguridad
        event = stripe.Webhook.construct_event(
            payload, sig_header, WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Manejar el evento de pago de suscripción exitoso
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        
        # Aquí puedes extraer el correo del cliente o ID para generar la API Key
        customer_email = session.get("customer_email") or session.get("customer_details", {}).get("email")
        
        # Generar una clave de API única para el suscriptor
        generated_api_key = f"ql_live_{os.urandom(16).hex()}"
        
        # TODO: Aquí puedes guardar 'generated_api_key' y asociarla a 'customer_email' en tu base de datos
        print(f"Suscripción exitosa para: {customer_email}. API Key generada: {generated_api_key}")

    return {"status": "success"}