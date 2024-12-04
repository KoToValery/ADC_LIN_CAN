from flask import Flask, send_from_directory, Response
import os

app = Flask(__name__)

# Добавяме поддръжка на ingress base path
base_path = os.environ.get('INGRESS_PATH', '')

@app.route(f'{base_path}/')
def dashboard():
    return send_from_directory('.', 'index.html')

@app.route(f'{base_path}/health')
def health():
    return Response(status=200)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8099)