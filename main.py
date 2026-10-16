from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import json

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        response = {"status": "online", "message": "API de Quantum Logistics funcionando correctamente"}
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        # Aquí puedes procesar webhooks de Stripe u otras peticiones POST
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"received": True}).encode("utf-8"))

def run():
    port = int(os.environ.get("PORT", 10000))
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, SimpleHandler)
    print(f"Servidor corriendo en el puerto {port}")
    httpd.serve_forever()

if __name__ == '__main__':
    run()