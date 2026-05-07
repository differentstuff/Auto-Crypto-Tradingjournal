import os

from flask import Flask, render_template

from database import init_db, get_conn
from importer import import_folder

import bitget_sync
import scanner_scheduler

from routes.journal   import bp as journal_bp
from routes.analytics import bp as analytics_bp
from routes.market    import bp as market_bp
from routes.calls     import bp as calls_bp
from routes.limits    import bp as limits_bp
from routes.live      import bp as live_bp
from routes.sync      import bp as sync_bp
from routes.scanner   import bp as scanner_bp
from routes.hindsight import bp as hindsight_bp

# ── app setup ──────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB upload limit

app.register_blueprint(journal_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(market_bp)
app.register_blueprint(calls_bp)
app.register_blueprint(limits_bp)
app.register_blueprint(live_bp)
app.register_blueprint(sync_bp)
app.register_blueprint(scanner_bp)
app.register_blueprint(hindsight_bp)


@app.route("/")
def index():
    return render_template("index.html")


# ── startup ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    # Auto-import CSV data if DB is empty
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0:
        csv_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
        if csv_files:
            print(f"[Startup] DB empty, auto-importing {len(csv_files)} CSV files from data/")
            import_folder(DATA_DIR, conn)
    conn.close()

    bitget_sync.start_background_sync()
    scanner_scheduler.start()

    port = int(os.environ.get("PORT", 8082))
    print(f"[App] Trading Journal running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
