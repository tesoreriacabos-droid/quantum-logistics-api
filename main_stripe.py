import os
from fastapi import FastAPI, Request, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
import stripe

app = FastAPI(
    title="Quantum Logistics API",
    version="1.0.0",
    description="API de optimización logística con autenticación basada en Stripe."
)

# Configurar Stripe con la llave secreta desde las variables de entorno de Render
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Configuración de seguridad para requerir API Key en los endpoints de logística
API_KEY_NAME = "access_token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# Simulación de una base de datos en memoria para guardar las llaves activas de clientes
# (En producción, esto se conecta a una base de datos como PostgreSQL o MongoDB)
ACTIVE_API_KEYS = set()

def get_api_key(api_key: str = Security(api_key_header)):
    """Valida que la llave de API enviada en el header sea válida."""
    if api_key in ACTIVE_API_KEYS:
        return api_key
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Could not validate credentials. Please subscribe via Stripe to get a valid API Key."
    )

@app.get("/")
def read_root():
    return {
        "status": "online", 
        "message": "Quantum Logistics Optimization API is running."
    }

# --- 1. MÓDULO DE STRIPE (WEBHOOKS) ---
@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Cuando el pago de la suscripción se completa exitosamente en Stripe
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_email") or session.get("customer_details", {}).get("email")
        
        # Generar una clave de API única para el cliente
        generated_api_key = f"ql_live_{os.urandom(16).hex()}"
        
        # Guardarla en nuestra lista de llaves activas
        ACTIVE_API_KEYS.add(generated_api_key)
        
        print(f"¡Suscripción exitosa! Cliente: {customer_email} | API Key generada: {generated_api_key}")

    return {"status": "success"}

# --- 2. MÓDULO DE LOGÍSTICA CUÁNTICA (PROTEGIDO POR API KEY) ---
@app.post("/optimize-route")
def optimize_route(data: dict, api_key: str = Depends(get_api_key)):
    """
    Endpoint protegido: Recibe ubicaciones o puntos de entrega y 
    simula un cálculo de optimización logística avanzado.
    """
    destinations = data.get("destinations", [])
    
    if not destinations:
        raise HTTPException(status_code=400, detail="Se requiere una lista de destinos para optimizar.")
    
    # Simulación de un resultado de optimización de rutas cuánticas / bio-inspiradas
    optimized_path = sorted(destinations) # Lógica de ejemplo
    
    return {
        "status": "success",
        "algorithm": "Quantum-Inspired Routing Simulation",
        "total_stops": len(destinations),
        "optimized_route": optimized_path,
        "estimated_fuel_savings_percent": 18.5
    }