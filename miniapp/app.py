import base64
import random
import os
import secrets
import sqlite3
import time
from pathlib import Path

import base58
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data.db")))
CHALLENGE_TTL_SECONDS = int(os.getenv("CHALLENGE_TTL_SECONDS", "300"))
GAME_TICK_SECONDS = int(os.getenv("GAME_TICK_SECONDS", "300"))
DECAY_PER_TICK = int(os.getenv("DECAY_PER_TICK", "1"))
BASE_PASSIVE_XP_PER_HOUR = int(os.getenv("BASE_PASSIVE_XP_PER_HOUR", "10"))
CASE_COST_XP = int(os.getenv("CASE_COST_XP", "20000"))
BOOST_DURATION_SECONDS = int(os.getenv("BOOST_DURATION_SECONDS", "3600"))
ALLOW_DEV_LOGIN = os.getenv("ALLOW_DEV_LOGIN", "1") == "1"

ACCESSORY_CATALOG = [
    {"id": "antenna_basic", "name": "Basic Antenna", "cost_xp": 400, "bonus_xp_per_hour": 5},
    {"id": "helmet_neo", "name": "Neo Helmet", "cost_xp": 1500, "bonus_xp_per_hour": 10},
    {"id": "blaster_mini", "name": "Mini Blaster", "cost_xp": 5000, "bonus_xp_per_hour": 20},
    {"id": "jetpack_ultra", "name": "Ultra Jetpack", "cost_xp": 12000, "bonus_xp_per_hour": 35},
]

CASE_REWARDS = [
    {"type": "boost", "value": 3, "label": "XP x3 (1 hour)"},
    {"type": "xp", "value": 10000, "label": "10 000 XP"},
    {"type": "xp", "value": 20000, "label": "20 000 XP"},
    {"type": "xp", "value": 40000, "label": "40 000 XP"},
    {"type": "token", "value": 0.1, "label": "0.1 MGPT"},
    {"type": "token", "value": 0.3, "label": "0.3 MGPT"},
]


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_links (
                telegram_id TEXT PRIMARY KEY,
                wallet TEXT NOT NULL,
                linked_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL,
                wallet TEXT NOT NULL,
                challenge TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_auth_challenges_lookup
            ON auth_challenges (telegram_id, wallet, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_state (
                telegram_id TEXT PRIMARY KEY,
                points INTEGER NOT NULL,
                mood INTEGER NOT NULL,
                hunger INTEGER NOT NULL,
                hygiene INTEGER NOT NULL,
                energy INTEGER NOT NULL,
                last_tick INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_accessories (
                telegram_id TEXT NOT NULL,
                accessory_id TEXT NOT NULL,
                purchased_at INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, accessory_id)
            )
            """
        )
        for statement in (
            "ALTER TABLE player_state ADD COLUMN passive_xp_per_hour INTEGER NOT NULL DEFAULT 10",
            "ALTER TABLE player_state ADD COLUMN last_passive_tick INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE player_state ADD COLUMN boost_multiplier INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE player_state ADD COLUMN boost_until INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE player_state ADD COLUMN mgpt_balance REAL NOT NULL DEFAULT 0",
        ):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass


def cleanup_old_challenges(conn: sqlite3.Connection) -> None:
    threshold = int(time.time()) - (CHALLENGE_TTL_SECONDS * 3)
    conn.execute("DELETE FROM auth_challenges WHERE created_at < ?", (threshold,))


def is_valid_wallet(wallet: str) -> bool:
    try:
        decoded = base58.b58decode(wallet)
    except ValueError:
        return False
    return len(decoded) == 32


def clamp_stat(value: int) -> int:
    return max(0, min(100, value))


def ensure_player_state(conn: sqlite3.Connection, telegram_id: str) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO player_state (
            telegram_id, points, mood, hunger, hygiene, energy, last_tick,
            passive_xp_per_hour, last_passive_tick, boost_multiplier, boost_until, mgpt_balance
        )
        VALUES (?, 0, 80, 80, 80, 80, ?, ?, ?, 1, 0, 0)
        ON CONFLICT(telegram_id) DO NOTHING
        """,
        (telegram_id, now, BASE_PASSIVE_XP_PER_HOUR, now),
    )


def apply_decay(conn: sqlite3.Connection, telegram_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT telegram_id, points, mood, hunger, hygiene, energy, last_tick,
               passive_xp_per_hour, last_passive_tick, boost_multiplier, boost_until, mgpt_balance
        FROM player_state
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    ).fetchone()
    if not row:
        raise ValueError("player state not found")

    now = int(time.time())
    elapsed = max(0, now - int(row["last_tick"]))
    ticks = elapsed // GAME_TICK_SECONDS
    if ticks <= 0:
        return row

    decay = ticks * DECAY_PER_TICK
    mood = clamp_stat(int(row["mood"]) - decay)
    hunger = clamp_stat(int(row["hunger"]) - decay)
    hygiene = clamp_stat(int(row["hygiene"]) - decay)
    energy = clamp_stat(int(row["energy"]) - decay)
    new_last_tick = int(row["last_tick"]) + (ticks * GAME_TICK_SECONDS)

    conn.execute(
        """
        UPDATE player_state
        SET mood = ?, hunger = ?, hygiene = ?, energy = ?, last_tick = ?
        WHERE telegram_id = ?
        """,
        (mood, hunger, hygiene, energy, new_last_tick, telegram_id),
    )
    return conn.execute(
        """
        SELECT telegram_id, points, mood, hunger, hygiene, energy, last_tick,
               passive_xp_per_hour, last_passive_tick, boost_multiplier, boost_until, mgpt_balance
        FROM player_state
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    ).fetchone()


def compute_passive_rate(conn: sqlite3.Connection, telegram_id: str) -> int:
    bonus = conn.execute(
        """
        SELECT COALESCE(SUM(a.bonus), 0) AS total_bonus
        FROM (
            SELECT CASE accessory_id
                WHEN 'antenna_basic' THEN 5
                WHEN 'helmet_neo' THEN 10
                WHEN 'blaster_mini' THEN 20
                WHEN 'jetpack_ultra' THEN 35
                ELSE 0
            END AS bonus
            FROM player_accessories
            WHERE telegram_id = ?
        ) a
        """,
        (telegram_id,),
    ).fetchone()
    return BASE_PASSIVE_XP_PER_HOUR + int(bonus["total_bonus"])


def apply_passive_xp(conn: sqlite3.Connection, telegram_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT telegram_id, points, mood, hunger, hygiene, energy, last_tick,
               passive_xp_per_hour, last_passive_tick, boost_multiplier, boost_until, mgpt_balance
        FROM player_state
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    ).fetchone()
    if not row:
        raise ValueError("player state not found")

    now = int(time.time())
    passive_rate = compute_passive_rate(conn, telegram_id)
    last_passive_tick = int(row["last_passive_tick"]) or int(row["last_tick"])
    elapsed = max(0, now - last_passive_tick)
    hours = elapsed // 3600
    gained = 0

    if hours > 0:
        boost_until = int(row["boost_until"])
        boost_multiplier = max(1, int(row["boost_multiplier"]))
        for i in range(1, hours + 1):
            hour_end = last_passive_tick + (i * 3600)
            if boost_until > 0 and hour_end <= boost_until:
                gained += passive_rate * boost_multiplier
            else:
                gained += passive_rate

        new_points = int(row["points"]) + gained
        new_last_passive_tick = last_passive_tick + (hours * 3600)
        conn.execute(
            """
            UPDATE player_state
            SET points = ?, last_passive_tick = ?, passive_xp_per_hour = ?
            WHERE telegram_id = ?
            """,
            (new_points, new_last_passive_tick, passive_rate, telegram_id),
        )
    else:
        conn.execute(
            "UPDATE player_state SET passive_xp_per_hour = ? WHERE telegram_id = ?",
            (passive_rate, telegram_id),
        )

    return conn.execute(
        """
        SELECT telegram_id, points, mood, hunger, hygiene, energy, last_tick,
               passive_xp_per_hour, last_passive_tick, boost_multiplier, boost_until, mgpt_balance
        FROM player_state
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    ).fetchone()


def row_to_state(row: sqlite3.Row) -> dict:
    mood = int(row["mood"])
    hunger = int(row["hunger"])
    hygiene = int(row["hygiene"])
    energy = int(row["energy"])
    health = int((mood + hunger + hygiene + energy) / 4)
    return {
        "xp": int(row["points"]),
        "mgpt_balance": round(float(row["mgpt_balance"]), 4),
        "passive_xp_per_hour": int(row["passive_xp_per_hour"]),
        "boost_multiplier": int(row["boost_multiplier"]),
        "boost_until": int(row["boost_until"]),
        "stats": {
            "mood": mood,
            "hunger": hunger,
            "hygiene": hygiene,
            "energy": energy,
            "health": health,
        },
    }


def get_owned_accessories(conn: sqlite3.Connection, telegram_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT accessory_id FROM player_accessories WHERE telegram_id = ?",
        (telegram_id,),
    ).fetchall()
    return {str(r["accessory_id"]) for r in rows}


def accessory_catalog_with_ownership(conn: sqlite3.Connection, telegram_id: str) -> list[dict]:
    owned = get_owned_accessories(conn, telegram_id)
    return [
        {
            **item,
            "owned": item["id"] in owned,
        }
        for item in ACCESSORY_CATALOG
    ]


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/auth/challenge")
def create_challenge():
    payload = request.get_json(silent=True) or {}
    wallet = str(payload.get("wallet", "")).strip()
    telegram_id = str(payload.get("telegram_id", "")).strip()
    now = int(time.time())

    if not wallet or not telegram_id:
        return jsonify({"ok": False, "error": "wallet and telegram_id are required"}), 400
    if not is_valid_wallet(wallet):
        return jsonify({"ok": False, "error": "invalid wallet format"}), 400

    nonce = secrets.token_hex(16)
    challenge = (
        "MyPiska Wallet Login\n"
        f"wallet:{wallet}\n"
        f"telegram_id:{telegram_id}\n"
        f"nonce:{nonce}\n"
        f"ts:{now}"
    )
    with get_db() as conn:
        cleanup_old_challenges(conn)
        conn.execute(
            """
            INSERT INTO auth_challenges (telegram_id, wallet, challenge, created_at, used)
            VALUES (?, ?, ?, ?, 0)
            """,
            (telegram_id, wallet, challenge, now),
        )

    return jsonify({"ok": True, "challenge": challenge, "expires_in": CHALLENGE_TTL_SECONDS})


@app.post("/api/auth/verify")
def verify_login():
    payload = request.get_json(silent=True) or {}
    wallet = str(payload.get("wallet", "")).strip()
    telegram_id = str(payload.get("telegram_id", "")).strip()
    challenge = str(payload.get("challenge", "")).strip()
    signature_b64 = str(payload.get("signature", "")).strip()
    now = int(time.time())

    if not wallet or not telegram_id or not challenge or not signature_b64:
        return jsonify({"ok": False, "error": "missing fields"}), 400
    if not is_valid_wallet(wallet):
        return jsonify({"ok": False, "error": "invalid wallet format"}), 400

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, created_at
            FROM auth_challenges
            WHERE telegram_id = ? AND wallet = ? AND challenge = ? AND used = 0
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id, wallet, challenge),
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "challenge not found"}), 400
        if now - int(row["created_at"]) > CHALLENGE_TTL_SECONDS:
            return jsonify({"ok": False, "error": "challenge expired"}), 400

        try:
            pubkey_bytes = base58.b58decode(wallet)
            signature_bytes = base64.b64decode(signature_b64)
            VerifyKey(pubkey_bytes).verify(challenge.encode("utf-8"), signature_bytes)
        except (ValueError, BadSignatureError):
            return jsonify({"ok": False, "error": "invalid signature"}), 400

        conn.execute("UPDATE auth_challenges SET used = 1 WHERE id = ?", (row["id"],))
        conn.execute(
            """
            INSERT INTO wallet_links (telegram_id, wallet, linked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET wallet = excluded.wallet, linked_at = excluded.linked_at
            """,
            (telegram_id, wallet, now),
        )
        ensure_player_state(conn, telegram_id)

    return jsonify({"ok": True, "wallet": wallet})


@app.get("/api/auth/status")
def auth_status():
    telegram_id = str(request.args.get("telegram_id", "")).strip()
    if not telegram_id:
        return jsonify({"ok": False, "error": "telegram_id is required"}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT wallet FROM wallet_links WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    if not row:
        return jsonify({"ok": True, "authorized": False, "wallet": None})
    return jsonify({"ok": True, "authorized": True, "wallet": row["wallet"]})


@app.post("/api/auth/unlink")
def unlink_wallet():
    payload = request.get_json(silent=True) or {}
    telegram_id = str(payload.get("telegram_id", "")).strip()
    if not telegram_id:
        return jsonify({"ok": False, "error": "telegram_id is required"}), 400

    with get_db() as conn:
        conn.execute("DELETE FROM wallet_links WHERE telegram_id = ?", (telegram_id,))
    return jsonify({"ok": True})


@app.post("/api/auth/dev-login")
def dev_login():
    if not ALLOW_DEV_LOGIN:
        return jsonify({"ok": False, "error": "dev login disabled"}), 403

    payload = request.get_json(silent=True) or {}
    telegram_id = str(payload.get("telegram_id", "")).strip()
    if not telegram_id:
        return jsonify({"ok": False, "error": "telegram_id is required"}), 400

    now = int(time.time())
    wallet = f"dev_wallet_{telegram_id}"
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO wallet_links (telegram_id, wallet, linked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET wallet = excluded.wallet, linked_at = excluded.linked_at
            """,
            (telegram_id, wallet, now),
        )
        ensure_player_state(conn, telegram_id)
    return jsonify({"ok": True, "wallet": wallet})


@app.get("/api/game/state")
def game_state():
    telegram_id = str(request.args.get("telegram_id", "")).strip()
    if not telegram_id:
        return jsonify({"ok": False, "error": "telegram_id is required"}), 400

    with get_db() as conn:
        wallet_row = conn.execute(
            "SELECT wallet FROM wallet_links WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not wallet_row:
            return jsonify({"ok": False, "error": "not authorized"}), 403
        ensure_player_state(conn, telegram_id)
        state_row = apply_decay(conn, telegram_id)
        state_row = apply_passive_xp(conn, telegram_id)
        catalog = accessory_catalog_with_ownership(conn, telegram_id)

    return jsonify(
        {
            "ok": True,
            "wallet": wallet_row["wallet"],
            "state": row_to_state(state_row),
            "accessories": catalog,
            "case_cost_xp": CASE_COST_XP,
            "case_rewards": CASE_REWARDS,
        }
    )


@app.post("/api/game/action")
def game_action():
    payload = request.get_json(silent=True) or {}
    telegram_id = str(payload.get("telegram_id", "")).strip()
    action = str(payload.get("action", "")).strip().lower()
    if not telegram_id or not action:
        return jsonify({"ok": False, "error": "telegram_id and action are required"}), 400

    actions = {
        "fun": {"mood": 18, "hunger": -4, "hygiene": -2, "energy": -6, "points": 4},
        "feed": {"mood": 5, "hunger": 22, "hygiene": -1, "energy": 2, "points": 3},
        "toilet": {"mood": 2, "hunger": 0, "hygiene": 24, "energy": -2, "points": 2},
        "sleep": {"mood": 4, "hunger": -3, "hygiene": 0, "energy": 24, "points": 3},
    }
    if action not in actions:
        return jsonify({"ok": False, "error": "invalid action"}), 400

    with get_db() as conn:
        wallet_row = conn.execute(
            "SELECT wallet FROM wallet_links WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not wallet_row:
            return jsonify({"ok": False, "error": "not authorized"}), 403

        ensure_player_state(conn, telegram_id)
        row = apply_decay(conn, telegram_id)
        row = apply_passive_xp(conn, telegram_id)
        effect = actions[action]

        mood = clamp_stat(int(row["mood"]) + effect["mood"])
        hunger = clamp_stat(int(row["hunger"]) + effect["hunger"])
        hygiene = clamp_stat(int(row["hygiene"]) + effect["hygiene"])
        energy = clamp_stat(int(row["energy"]) + effect["energy"])
        points = int(row["points"]) + effect["points"]
        if min(mood, hunger, hygiene, energy) >= 60:
            points += 2

        conn.execute(
            """
            UPDATE player_state
            SET points = ?, mood = ?, hunger = ?, hygiene = ?, energy = ?
            WHERE telegram_id = ?
            """,
            (points, mood, hunger, hygiene, energy, telegram_id),
        )
        updated = conn.execute(
            """
            SELECT telegram_id, points, mood, hunger, hygiene, energy, last_tick,
                   passive_xp_per_hour, last_passive_tick, boost_multiplier, boost_until, mgpt_balance
            FROM player_state
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
        catalog = accessory_catalog_with_ownership(conn, telegram_id)

    return jsonify(
        {
            "ok": True,
            "wallet": wallet_row["wallet"],
            "state": row_to_state(updated),
            "accessories": catalog,
            "case_cost_xp": CASE_COST_XP,
        }
    )


@app.post("/api/game/buy-accessory")
def buy_accessory():
    payload = request.get_json(silent=True) or {}
    telegram_id = str(payload.get("telegram_id", "")).strip()
    accessory_id = str(payload.get("accessory_id", "")).strip()
    if not telegram_id or not accessory_id:
        return jsonify({"ok": False, "error": "telegram_id and accessory_id are required"}), 400

    accessory = next((a for a in ACCESSORY_CATALOG if a["id"] == accessory_id), None)
    if not accessory:
        return jsonify({"ok": False, "error": "invalid accessory"}), 400

    with get_db() as conn:
        wallet_row = conn.execute(
            "SELECT wallet FROM wallet_links WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not wallet_row:
            return jsonify({"ok": False, "error": "not authorized"}), 403

        ensure_player_state(conn, telegram_id)
        row = apply_decay(conn, telegram_id)
        row = apply_passive_xp(conn, telegram_id)

        owned = conn.execute(
            """
            SELECT 1 FROM player_accessories
            WHERE telegram_id = ? AND accessory_id = ?
            """,
            (telegram_id, accessory_id),
        ).fetchone()
        if owned:
            return jsonify({"ok": False, "error": "already purchased"}), 400
        if int(row["points"]) < int(accessory["cost_xp"]):
            return jsonify({"ok": False, "error": "not enough xp"}), 400

        now = int(time.time())
        conn.execute(
            """
            INSERT INTO player_accessories (telegram_id, accessory_id, purchased_at)
            VALUES (?, ?, ?)
            """,
            (telegram_id, accessory_id, now),
        )
        new_rate = compute_passive_rate(conn, telegram_id)
        conn.execute(
            """
            UPDATE player_state
            SET points = points - ?, passive_xp_per_hour = ?
            WHERE telegram_id = ?
            """,
            (int(accessory["cost_xp"]), new_rate, telegram_id),
        )
        updated = conn.execute(
            """
            SELECT telegram_id, points, mood, hunger, hygiene, energy, last_tick,
                   passive_xp_per_hour, last_passive_tick, boost_multiplier, boost_until, mgpt_balance
            FROM player_state
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
        catalog = accessory_catalog_with_ownership(conn, telegram_id)

    return jsonify({"ok": True, "state": row_to_state(updated), "accessories": catalog})


@app.post("/api/game/open-case")
def open_case():
    payload = request.get_json(silent=True) or {}
    telegram_id = str(payload.get("telegram_id", "")).strip()
    if not telegram_id:
        return jsonify({"ok": False, "error": "telegram_id is required"}), 400

    with get_db() as conn:
        wallet_row = conn.execute(
            "SELECT wallet FROM wallet_links WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not wallet_row:
            return jsonify({"ok": False, "error": "not authorized"}), 403

        ensure_player_state(conn, telegram_id)
        row = apply_decay(conn, telegram_id)
        row = apply_passive_xp(conn, telegram_id)

        if int(row["points"]) < CASE_COST_XP:
            return jsonify({"ok": False, "error": "not enough xp for case"}), 400

        reward = random.choice(CASE_REWARDS)
        conn.execute(
            "UPDATE player_state SET points = points - ? WHERE telegram_id = ?",
            (CASE_COST_XP, telegram_id),
        )
        if reward["type"] == "xp":
            conn.execute(
                "UPDATE player_state SET points = points + ? WHERE telegram_id = ?",
                (int(reward["value"]), telegram_id),
            )
        elif reward["type"] == "token":
            conn.execute(
                "UPDATE player_state SET mgpt_balance = mgpt_balance + ? WHERE telegram_id = ?",
                (float(reward["value"]), telegram_id),
            )
        elif reward["type"] == "boost":
            now = int(time.time())
            conn.execute(
                """
                UPDATE player_state
                SET boost_multiplier = ?, boost_until = ?
                WHERE telegram_id = ?
                """,
                (int(reward["value"]), now + BOOST_DURATION_SECONDS, telegram_id),
            )

        updated = conn.execute(
            """
            SELECT telegram_id, points, mood, hunger, hygiene, energy, last_tick,
                   passive_xp_per_hour, last_passive_tick, boost_multiplier, boost_until, mgpt_balance
            FROM player_state
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
        catalog = accessory_catalog_with_ownership(conn, telegram_id)

    return jsonify(
        {
            "ok": True,
            "reward": reward,
            "state": row_to_state(updated),
            "accessories": catalog,
            "case_cost_xp": CASE_COST_XP,
        }
    )


init_db()


if __name__ == "__main__":
    host = os.getenv("WEBAPP_HOST", "0.0.0.0")
    port = int(os.getenv("WEBAPP_PORT", "8080"))
    debug = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    app.run(host=host, port=port, debug=debug)
