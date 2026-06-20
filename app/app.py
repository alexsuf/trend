from flask import Flask
import socket

app = Flask(__name__)

@app.route("/")
def index():
    hostname = socket.gethostname()

    return f"""
    <h1>Trend Research MVP</h1>

    <p>Flask работает.</p>

    <p>Имя хоста: {hostname}</p>
    """

@app.route("/health")
def health():
    return {"status": "ok"}