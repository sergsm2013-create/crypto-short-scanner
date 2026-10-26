import threading
from flask import Flask
import scanner

app = Flask(__name__)

@app.route('/')
def health():
    return "Bot is running", 200

if __name__ == '__main__':
    # Запускаем сканер в фоне
    threading.Thread(target=scanner.main, daemon=True).start()
    # Запускаем веб-сервер для проверки жизнеспособности (Render требует этого)
    app.run(host='0.0.0.0', port=10000)
