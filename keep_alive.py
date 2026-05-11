from flask import Flask, jsonify
from flask_cors import CORS
from threading import Thread
import json
import time
import secrets
import requests

app = Flask('')
CORS(app)

# TERA RENDER APP URL YAHAN DAALNA
RENDER_PUBLIC_URL = "https://tera-bot.onrender.com" 

@app.route('/')
def home():
    return "Bot is awake!"

@app.route('/api/stats')
def stats():
    try:
        with open('stats.json', 'r') as f:
            data = json.load(f)
            return jsonify(data)
    except Exception:
        return jsonify({"servers": "Loading...", "ping": "..."})

def run():
    app.run(host='0.0.0.0', port=8080)

def crypto_self_ping():
    """Cryptographic random interval (5 to 10 mins) par public URL ko ping karega."""
    while True:
        # secrets.randbelow(301) gives 0 to 300. 
        # Total wait time = 300 (5 mins) + random up to 300 = 5 to 10 mins.
        sleep_seconds = 300 + secrets.randbelow(301) 
        
        time.sleep(sleep_seconds)
        
        try:
            # Public URL ko hit kar rahe hain taki router ko lage external traffic hai
            requests.get(RENDER_PUBLIC_URL)
            print(f"🔄 Crypto-ping successful (Waited {sleep_seconds}s)")
        except Exception as e:
            print(f"⚠️ Crypto-ping failed: {e}")

def keep_alive():
    # Start Flask Server
    t = Thread(target=run)
    t.start()
    
    # Start Self-Ping Logic
    ping_thread = Thread(target=crypto_self_ping)
    ping_thread.start()