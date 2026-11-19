from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import smtplib
import ssl
import secrets
import sqlite3
import sys
import time
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default as email_policy
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape as html_escape
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "nextbuy.db"
UPLOAD_DIR = ROOT / "uploads"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 128 * 1024
HOST = os.environ.get("NEXTBUY_HOST", "127.0.0.1")
PORT = int(os.environ.get("NEXTBUY_PORT", "8000"))
ADMIN_EMAIL = os.environ.get("NEXTBUY_ADMIN_EMAIL", "admin@nextbuy.pk").strip().lower()
ADMIN_PASSWORD = os.environ.get("NEXTBUY_ADMIN_PASSWORD", "admin123")
BASE_URL = os.environ.get("NEXTBUY_BASE_URL", "http://localhost:8000").rstrip("/")
SESSION_DAYS = 14
MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 300
_failed_logins: dict[str, list[float]] = {}


def login_locked(key: str) -> bool:
    cutoff = time.time() - LOCKOUT_SECONDS
    attempts = [t for t in _failed_logins.get(key, []) if t > cutoff]
    _failed_logins[key] = attempts
    return len(attempts) >= MAX_LOGIN_FAILURES


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_column(con: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    present = {row[1] for row in con.execute("PRAGMA table_info(" + table + ")")}
    if column not in present:
        con.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + declaration)


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=20, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
    return salt.hex() + ":" + digest.hex()


def password_matches(password: str, encoded: str) -> bool:
    try:
        salt_hex, digest_hex = encoded.split(":", 1)
        candidate = password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
        return hmac.compare_digest(candidate, digest_hex)
    except (ValueError, TypeError):
        return False


def init_db() -> None:
    with db() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE COLLATE NOCASE,
            phone TEXT UNIQUE,
            password_hash TEXT,
            role TEXT NOT NULL DEFAULT 'customer',
            joined TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER NOT NULL,
            old_price INTEGER NOT NULL DEFAULT 0,
            image TEXT NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            rating REAL NOT NULL DEFAULT 4.5,
            reviews INTEGER NOT NULL DEFAULT 0,
            description TEXT NOT NULL DEFAULT '',
            colors_json TEXT NOT NULL DEFAULT '[]',
            sizes_json TEXT NOT NULL DEFAULT '[]',
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            customer TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL,
            city TEXT NOT NULL,
            address TEXT NOT NULL,
            payment TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            subtotal INTEGER NOT NULL,
            shipping INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'Online',
            items_json TEXT NOT NULL,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS phone_otps (
            phone TEXT PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS phone_otp_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            requested TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS categories (
            name TEXT PRIMARY KEY,
            icon TEXT NOT NULL DEFAULT 'fa-bag-shopping',
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE COLLATE NOCASE,
            kind TEXT NOT NULL,
            amount INTEGER NOT NULL,
            min_order INTEGER NOT NULL DEFAULT 0,
            expires TEXT NOT NULL DEFAULT '',
            usage_limit INTEGER NOT NULL DEFAULT 0,
            uses INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image TEXT NOT NULL,
            headline TEXT NOT NULL DEFAULT '',
            button_label TEXT NOT NULL DEFAULT 'Shop Now',
            button_link TEXT NOT NULL DEFAULT '#products',
            sort_order INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            text TEXT NOT NULL DEFAULT '',
            visible INTEGER NOT NULL DEFAULT 1,
            created TEXT NOT NULL,
            UNIQUE(product_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
        CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created);
        CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id,visible);
        CREATE INDEX IF NOT EXISTS idx_otp_phone_time ON phone_otp_requests(phone,requested);
        """)
        ensure_column(con, "products", "gallery_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(con, "orders", "coupon_code", "TEXT NOT NULL DEFAULT ''")
        ensure_column(con, "orders", "discount", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "orders", "payment_status", "TEXT NOT NULL DEFAULT 'Unpaid'")
        ensure_column(con, "orders", "payment_provider", "TEXT NOT NULL DEFAULT ''")
        ensure_column(con, "orders", "payment_ref", "TEXT NOT NULL DEFAULT ''")
        ensure_column(con, "phone_otps", "attempts", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "phone_otps", "created", "TEXT NOT NULL DEFAULT ''")
        for key, value in (("shipping_default", "0"), ("free_delivery_above", "0"),
                           ("city_charges", "{}"), ("low_stock_limit", "5")):
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        default_categories = [("Electronics", "fa-bolt"), ("Mobile Accessories", "fa-mobile-screen-button"),
                              ("Shoes", "fa-shoe-prints"), ("Perfume", "fa-spray-can-sparkles"),
                              ("Watches", "fa-clock"), ("Clothes", "fa-shirt"), ("Other", "fa-bag-shopping")]
        if not con.execute("SELECT 1 FROM settings WHERE key='categories_initialized'").fetchone():
            if not con.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
                for idx, (name, icon) in enumerate(default_categories):
                    con.execute("INSERT OR IGNORE INTO categories(name,icon,sort_order) VALUES(?,?,?)", (name, icon, idx))
            con.execute("INSERT INTO settings(key,value) VALUES('categories_initialized','1')")
        for row in con.execute("SELECT DISTINCT category FROM products").fetchall():
            con.execute("INSERT OR IGNORE INTO categories(name,icon,sort_order) VALUES(?,?,?)",
                        (row["category"], "fa-bag-shopping", 100))
        for row in con.execute("SELECT id FROM products").fetchall():
            recalculate_product_rating(con, row["id"])
        con.execute("DELETE FROM sessions WHERE expires<?", (now_iso(),))
        con.execute("DELETE FROM phone_otps WHERE expires<?", (now_iso(),))
        admin = con.execute("SELECT id, role FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
        if not admin:
            con.execute("INSERT INTO users(name,email,password_hash,role,joined) VALUES(?,?,?,?,?)",
                        ("NextBuy Admin", ADMIN_EMAIL, password_hash(ADMIN_PASSWORD), "admin", now_iso()))
        elif admin["role"] != "admin":
            con.execute("UPDATE users SET role='admin' WHERE id=?", (admin["id"],))


def public_user(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "email": row["email"] or "",
            "phone": row["phone"] or "", "role": row["role"], "joined": row["joined"]}


def public_product(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "category": row["category"],
            "price": row["price"], "oldPrice": row["old_price"], "image": row["image"],
            "stock": row["stock"], "rating": row["rating"], "reviews": row["reviews"],
            "description": row["description"], "colors": json.loads(row["colors_json"]),
            "sizes": json.loads(row["sizes_json"]),
            "gallery": json.loads(row["gallery_json"]) if "gallery_json" in row.keys() else []}


def public_order(row: sqlite3.Row) -> dict:
    return {"id": row["order_code"], "userId": row["user_id"], "customer": row["customer"],
            "email": row["email"], "phone": row["phone"], "city": row["city"],
            "address": row["address"], "payment": row["payment"], "status": row["status"],
            "subtotal": row["subtotal"], "shipping": row["shipping"], "discount": row["discount"],
            "couponCode": row["coupon_code"], "paymentStatus": row["payment_status"],
            "paymentProvider": row["payment_provider"], "paymentRef": row["payment_ref"],
            "total": row["total"], "source": row["source"],
            "items": json.loads(row["items_json"]), "date": row["created"]}


def safe_int(value, field: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(field + " must be a whole number.")
    if number < low or number > high:
        raise ValueError(field + " is out of range.")
    return number


def clean_product(data: dict) -> dict:
    name = str(data.get("name", "")).strip()[:120]
    category = str(data.get("category", "")).strip()[:80]
    image = str(data.get("image", "")).strip()[:1500]
    description = str(data.get("description", "")).strip()[:2500]
    if not name or not category or not image or not description:
        raise ValueError("Name, category, image and description are required.")
    if not (image.startswith("https://") or image.startswith("http://") or image.startswith("/")):
        raise ValueError("Image must be an HTTP or local URL.")
    try:
        rating = float(data.get("rating", 4.5))
    except (TypeError, ValueError):
        rating = 4.5
    if not math.isfinite(rating):
        rating = 4.5
    colors = data.get("colors", [])
    sizes = data.get("sizes", [])
    if not isinstance(colors, list): colors = []
    if not isinstance(sizes, list): sizes = []
    gallery = data.get("gallery", [])
    if not isinstance(gallery, list): gallery = []
    gallery = [str(x).strip()[:1500] for x in gallery[:10]
               if str(x).startswith(("https://", "http://", "/"))]
    return {"name": name, "category": category,
            "price": safe_int(data.get("price"), "Price", 1, 100_000_000),
            "old_price": safe_int(data.get("oldPrice", 0), "Old price", 0, 100_000_000),
            "image": image, "stock": safe_int(data.get("stock", 0), "Stock", 0, 1_000_000),
            "rating": max(0.0, min(5.0, rating)), "reviews": safe_int(data.get("reviews", 0), "Reviews", 0, 10_000_000),
            "description": description,
            "colors_json": json.dumps([str(x)[:40] for x in colors[:20]]),
            "sizes_json": json.dumps([str(x)[:40] for x in sizes[:20]]),
            "gallery_json": json.dumps(gallery)}


def send_email(to_address: str, subject: str, body: str) -> bool:
    host = os.environ.get("NEXTBUY_SMTP_HOST", "").strip()
    if not host or not to_address:
        return False
    try:
        message = EmailMessage()
        message["From"] = os.environ.get("NEXTBUY_SMTP_FROM", os.environ.get("NEXTBUY_SMTP_USER", ""))
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)
        port = int(os.environ.get("NEXTBUY_SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if os.environ.get("NEXTBUY_SMTP_TLS", "1") == "1":
                smtp.starttls(context=ssl.create_default_context())
            user = os.environ.get("NEXTBUY_SMTP_USER", "")
            password = os.environ.get("NEXTBUY_SMTP_PASSWORD", "")
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
        return True
    except Exception as exc:
        print("Email delivery failed: " + str(exc), file=sys.stderr)
        return False


def notify_order(order: dict, event: str) -> None:
    if order.get("email"):
        lines = ["Hello " + order["customer"] + ",", "", event,
                 "Order: " + order["id"], "Status: " + order["status"],
                 "Payment: " + order["payment"] + " (" + order["paymentStatus"] + ")",
                 "Subtotal: Rs. " + format(order["subtotal"], ","),
                 "Discount: Rs. " + format(order["discount"], ","),
                 "Delivery: Rs. " + format(order["shipping"], ","),
                 "Total: Rs. " + format(order["total"], ","), ""]
        lines.extend("- %s x %s" % (item["name"], item["qty"]) for item in order.get("items", []))
        lines.extend(["", "Delivering to: " + order["city"], order["address"]])
        send_email(order["email"], "NextBuy.pk order " + order["id"], "\n".join(lines))


def send_sms_code(phone: str, code: str) -> None:
    if os.environ.get("NEXTBUY_DEMO_OTP") == "1":
        print("DEMO OTP for %s: %s" % (phone, code))
        return
    endpoint = os.environ.get("NEXTBUY_SMS_PROVIDER_URL", "").strip()
    api_key = os.environ.get("NEXTBUY_SMS_API_KEY", "").strip()
    if not endpoint or not api_key:
        raise ValueError("SMS is not configured. Set provider URL and API key, or set NEXTBUY_DEMO_OTP=1 for local testing.")
    if not endpoint.startswith("https://"):
        raise ValueError("SMS provider URL must use HTTPS.")
    payload = json.dumps({"to": phone, "message": "Your NextBuy.pk code is " + code,
                          "code": code}).encode("utf-8")
    request = Request(endpoint, data=payload,
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key,
                               "X-API-Key": api_key}, method="POST")
    with urlopen(request, timeout=15) as response:
        if response.status >= 400:
            raise ValueError("SMS provider rejected the request.")


def setting_value(con: sqlite3.Connection, key: str, default: str = "") -> str:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def shipping_charge(con: sqlite3.Connection, city: str, subtotal: int) -> int:
    base = safe_int(setting_value(con, "shipping_default", "0"), "Delivery charge", 0, 1_000_000)
    threshold = safe_int(setting_value(con, "free_delivery_above", "0"), "Free delivery threshold", 0, 100_000_000)
    try:
        city_charges = json.loads(setting_value(con, "city_charges", "{}"))
    except json.JSONDecodeError:
        city_charges = {}
    charge = base
    if isinstance(city_charges, dict):
        for name, amount in city_charges.items():
            if str(name).strip().casefold() == city.strip().casefold():
                charge = safe_int(amount, "City delivery charge", 0, 1_000_000)
                break
    if threshold and subtotal >= threshold:
        return 0
    return charge


def coupon_discount(con: sqlite3.Connection, code: str, subtotal: int) -> tuple[str, int]:
    code = str(code or "").strip().upper()
    if not code:
        return "", 0
    if not re.fullmatch(r"[A-Z0-9_-]{2,32}", code):
        raise ValueError("Enter a valid coupon code.")
    coupon = con.execute("SELECT * FROM coupons WHERE code=? COLLATE NOCASE", (code,)).fetchone()
    if not coupon or not coupon["enabled"]:
        raise ValueError("This coupon is not available.")
    expires = coupon["expires"] or ""
    if expires:
        try:
            expiry_day = date.fromisoformat(expires[:10])
        except ValueError:
            raise ValueError("This coupon has an invalid expiry date.")
        if expiry_day < datetime.now(timezone.utc).date():
            raise ValueError("This coupon has expired.")
    if subtotal < coupon["min_order"]:
        raise ValueError("The cart does not meet this coupon's minimum order.")
    if coupon["usage_limit"] and coupon["uses"] >= coupon["usage_limit"]:
        raise ValueError("This coupon has reached its usage limit.")
    if coupon["kind"] == "percent":
        discount = subtotal * coupon["amount"] // 100
    else:
        discount = coupon["amount"]
    return coupon["code"], min(subtotal, max(0, discount))


def quote_order(con: sqlite3.Connection, items: list, city: str, coupon_code: str = "") -> dict:
    if not isinstance(items, list) or not items or len(items) > 50:
        raise ValueError("Add at least one item to your cart.")
    quantities: dict[int, int] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Cart item is invalid.")
        pid = safe_int(item.get("id"), "Product", 1, 2_000_000_000)
        qty = safe_int(item.get("qty"), "Quantity", 1, 1000)
        quantities[pid] = quantities.get(pid, 0) + qty
    product_rows = {}
    for pid, qty in quantities.items():
        product = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not product:
            raise ValueError("A product in this order is no longer available.")
        if product["stock"] < qty:
            raise ValueError("Not enough stock for " + product["name"] + ".")
        product_rows[pid] = product
    order_items = []
    subtotal = 0
    for item in items:
        pid = int(item["id"])
        product = product_rows[pid]
        qty = int(item["qty"])
        line_total = product["price"] * qty
        subtotal += line_total
        order_items.append({"id": pid, "name": product["name"], "price": product["price"], "qty": qty,
                            "size": str(item.get("size", ""))[:40], "color": str(item.get("color", ""))[:40],
                            "subtotal": line_total})
    saved_code, discount = coupon_discount(con, coupon_code, subtotal)
    shipping = shipping_charge(con, city, subtotal)
    return {"items": order_items, "quantities": quantities, "subtotal": subtotal,
            "discount": discount, "coupon_code": saved_code, "shipping": shipping,
            "total": subtotal - discount + shipping}


def create_order(con: sqlite3.Connection, data: dict, user_id: int | None, source: str, status: str) -> dict:
    customer = str(data.get("customer", "")).strip()[:120]
    phone = str(data.get("phone", "")).strip()[:40]
    email = str(data.get("email", "")).strip()[:180]
    city = str(data.get("city", "")).strip()[:100]
    address = str(data.get("address", "")).strip()[:500]
    payment_input = str(data.get("payment", "Cash on Delivery")).strip()[:50]
    allowed_payments = {"cash on delivery": "Cash on Delivery", "jazzcash": "JazzCash",
                        "easypaisa": "EasyPaisa"}
    if source == "POS":
        allowed_payments.update({"cash": "Cash", "card": "Card"})
    payment = allowed_payments.get(payment_input.casefold())
    if not payment:
        raise ValueError("Choose a supported payment method.")
    items = data.get("items", [])
    if not customer or (source == "Online" and not phone):
        raise ValueError("Customer and phone are required.")
    if source == "Online" and (not city or not address):
        raise ValueError("City and full address are required.")
    quote = quote_order(con, items, city, data.get("couponCode", "") if source == "Online" else "")
    if source != "Online":
        quote["shipping"] = 0
        quote["total"] = quote["subtotal"] - quote["discount"]
    for pid, qty in quote["quantities"].items():
        updated = con.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=?", (qty, pid, qty)).rowcount
        if updated != 1:
            raise ValueError("Product stock changed. Please refresh and try again.")
    provider = ""
    if payment.lower() in ("jazzcash", "jazzcash payment"):
        provider = "jazzcash"
    elif payment.lower() in ("easypaisa", "easypay"):
        provider = "easypaisa"
    payment_status = "Paid" if source == "POS" or status == "Delivered" else "Unpaid"
    if source == "Online" and provider:
        status = "Pending payment"
        payment_status = "Pending"
    if quote["coupon_code"]:
        con.execute("UPDATE coupons SET uses=uses+1 WHERE code=? COLLATE NOCASE", (quote["coupon_code"],))
    con.execute("""INSERT INTO orders(user_id,customer,email,phone,city,address,payment,status,subtotal,shipping,total,source,items_json,created,
                  coupon_code,discount,payment_status,payment_provider,payment_ref)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, customer, email, phone, city or "In-store", address or "Admin POS sale", payment,
                 status, quote["subtotal"], quote["shipping"], quote["total"], source,
                 json.dumps(quote["items"], ensure_ascii=False), now_iso(), quote["coupon_code"], quote["discount"],
                 payment_status, provider, ""))
    order_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    code = "NB-" + str(1000 + order_id)
    con.execute("UPDATE orders SET order_code=? WHERE id=?", (code, order_id))
    return public_order(con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

def public_settings(con: sqlite3.Connection) -> dict:
    try:
        city_charges = json.loads(setting_value(con, "city_charges", "{}"))
    except json.JSONDecodeError:
        city_charges = {}
    return {"shippingDefault": safe_int(setting_value(con, "shipping_default", "0"), "Delivery charge", 0, 1_000_000),
            "freeDeliveryAbove": safe_int(setting_value(con, "free_delivery_above", "0"), "Free delivery threshold", 0, 100_000_000),
            "cityCharges": city_charges if isinstance(city_charges, dict) else {},
            "lowStockLimit": safe_int(setting_value(con, "low_stock_limit", "5"), "Low stock limit", 0, 1_000_000),
            "whatsappNumber": re.sub(r"\D", "", os.environ.get("NEXTBUY_WHATSAPP_NUMBER", ""))[:15]}


def public_coupon(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "code": row["code"], "kind": row["kind"], "amount": row["amount"],
            "minOrder": row["min_order"], "expires": row["expires"], "usageLimit": row["usage_limit"],
            "uses": row["uses"], "enabled": bool(row["enabled"])}


def public_banner(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "image": row["image"], "headline": row["headline"],
            "buttonLabel": row["button_label"], "buttonLink": row["button_link"],
            "sortOrder": row["sort_order"], "enabled": bool(row["enabled"])}


def recalculate_product_rating(con: sqlite3.Connection, product_id: int) -> None:
    aggregate = con.execute("SELECT COUNT(*) AS count,COALESCE(AVG(rating),0) AS average FROM reviews WHERE product_id=? AND visible=1", (product_id,)).fetchone()
    con.execute("UPDATE products SET rating=?,reviews=? WHERE id=?", (aggregate["average"], aggregate["count"], product_id))


def validate_category(name: str, icon: str) -> tuple[str, str]:
    name = str(name or "").strip()[:60]
    icon = str(icon or "fa-bag-shopping").strip().lower()[:60]
    if not name or not re.fullmatch(r"[\w &'-]{1,60}", name, flags=re.UNICODE):
        raise ValueError("Enter a valid category name.")
    if not re.fullmatch(r"fa-[a-z0-9-]{2,50}", icon):
        raise ValueError("Enter a Font Awesome icon name such as fa-bolt.")
    return name, icon


def validate_coupon(data: dict) -> dict:
    code = str(data.get("code", "")).strip().upper()
    kind = str(data.get("kind", "percent")).strip().lower()
    if not re.fullmatch(r"[A-Z0-9_-]{2,32}", code):
        raise ValueError("Coupon code must use 2–32 letters, numbers, hyphens or underscores.")
    if kind not in ("percent", "fixed"):
        raise ValueError("Choose percent or fixed amount.")
    amount = safe_int(data.get("amount"), "Discount", 1, 100_000_000)
    if kind == "percent" and amount > 100:
        raise ValueError("Percent discount cannot exceed 100%.")
    expires = str(data.get("expires", "")).strip()[:10]
    if expires:
        validate_date_filter(expires, "Expiry date")
    return {"code": code, "kind": kind, "amount": amount,
            "min_order": safe_int(data.get("minOrder", 0), "Minimum order", 0, 100_000_000),
            "expires": expires,
            "usage_limit": safe_int(data.get("usageLimit", 0), "Usage limit", 0, 10_000_000),
            "enabled": 1 if data.get("enabled", True) else 0}


def validate_date_filter(value: str, label: str) -> None:
    if value:
        try: date.fromisoformat(value)
        except ValueError: raise ValueError(label + " must use YYYY-MM-DD format.")


def csv_cell(value) -> str:
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def report_data(con: sqlite3.Connection, start: str, end: str, period: str) -> dict:
    conditions = ["status='Delivered'"]
    params = []
    if start: conditions.append("substr(created,1,10)>=?"); params.append(start)
    if end: conditions.append("substr(created,1,10)<=?"); params.append(end)
    where = " AND ".join(conditions)
    orders = con.execute("SELECT * FROM orders WHERE " + where + " ORDER BY created", params).fetchall()
    total = sum(row["total"] for row in orders)
    if period == "weekly": key_expr = "strftime('%Y-%W',created)"
    elif period == "monthly": key_expr = "strftime('%Y-%m',created)"
    else: key_expr = "substr(created,1,10)"
    series_rows = con.execute("SELECT " + key_expr + " AS label,COALESCE(SUM(total),0) AS sales,COUNT(*) AS count FROM orders WHERE " + where + " GROUP BY label ORDER BY label", params).fetchall()
    product_totals = {}
    for order in orders:
        for item in json.loads(order["items_json"]):
            key = int(item["id"])
            if key not in product_totals: product_totals[key] = {"id": key, "name": item["name"], "quantity": 0, "sales": 0}
            product_totals[key]["quantity"] += int(item["qty"])
            product_totals[key]["sales"] += int(item["subtotal"])
    top = sorted(product_totals.values(), key=lambda item: (-item["quantity"], -item["sales"]))[:10]
    return {"period": period, "totalSales": total, "totalOrders": len(orders),
            "averageOrderValue": total // len(orders) if orders else 0,
            "series": [dict(row) for row in series_rows], "topProducts": top}


def invoice_html(order: dict) -> str:
    rows = "".join("<tr><td>%s</td><td>%s</td><td>Rs. %s</td><td>Rs. %s</td></tr>" %
                   (html_escape(str(item["name"])), item["qty"], format(item["price"], ","), format(item["subtotal"], ","))
                   for item in order["items"])
    return """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Invoice %s</title><style>body{font:14px Arial,sans-serif;max-width:800px;margin:30px auto;color:#222}header{display:flex;justify-content:space-between;border-bottom:3px solid #ff7a00;padding-bottom:14px}h1{font-size:25px}table{width:100%%;border-collapse:collapse;margin-top:24px}th,td{text-align:left;padding:10px;border-bottom:1px solid #ddd}th{background:#f6f6f6}.totals{margin:20px 0 20px auto;width:280px}.totals div{display:flex;justify-content:space-between;padding:5px}.grand{font-weight:bold;border-top:2px solid #222}.print{background:#ff7a00;color:#fff;padding:10px 16px;border:0;border-radius:4px}@media print{.print{display:none}body{margin:0}}</style></head><body><header><div><b>NextBuy.pk</b><p>Invoice / Packing Slip</p></div><div><h1>%s</h1><p>%s</p><p>%s</p></div></header><p><b>Customer:</b> %s<br><b>Phone:</b> %s<br><b>Ship to:</b> %s, %s</p><table><thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Line total</th></tr></thead><tbody>%s</tbody></table><div class='totals'><div><span>Subtotal</span><span>Rs. %s</span></div><div><span>Discount (%s)</span><span>- Rs. %s</span></div><div><span>Delivery</span><span>Rs. %s</span></div><div class='grand'><span>Total</span><span>Rs. %s</span></div></div><p>Payment: %s (%s) · Status: %s</p><button class='print' onclick='window.print()'>Print invoice</button></body></html>""" % (
        html_escape(order["id"]), html_escape(order["id"]), html_escape(order["date"]),
        html_escape(order["customer"]), html_escape(order["phone"]), html_escape(order["address"]), html_escape(order["city"]), rows,
        format(order["subtotal"], ","), html_escape(order["couponCode"] or "no coupon"), format(order["discount"], ","),
        format(order["shipping"], ","), format(order["total"], ","), html_escape(order["payment"]),
        html_escape(order["paymentStatus"]), html_escape(order["status"]))


class StoreHandler(BaseHTTPRequestHandler):
    server_version = "NextBuyLocal/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    def respond_bytes(self, body: bytes, content_type: str, status=HTTPStatus.OK, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def respond(self, payload, status=HTTPStatus.OK, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def respond_html(self, body: str, status=HTTPStatus.OK):
        return self.respond_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status)

    def read_form(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            raise ValueError("Request size is invalid.")
        if length <= 0 or length > 1_000_000:
            raise ValueError("Request body is empty or too large.")
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request is too large.")
        if length <= 0:
            return {}
        try:
            result = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Request body must be valid JSON.")
        if not isinstance(result, dict):
            raise ValueError("Request body must be an object.")
        return result

    def read_image_upload(self) -> tuple[bytes, str]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            raise ValueError("Upload size is invalid.")
        if length <= 0:
            raise ValueError("Choose an image to upload.")
        if length > MAX_UPLOAD_REQUEST_BYTES:
            raise ValueError("Image must be 2 MB or smaller.")
        content_type = self.headers.get("Content-Type", "")
        if "\r" in content_type or "\n" in content_type:
            raise ValueError("Upload format is invalid.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Upload was incomplete.")
        try:
            message = BytesParser(policy=email_policy).parsebytes(
                ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii") + body
            )
        except (UnicodeEncodeError, ValueError):
            raise ValueError("Upload format is invalid.")
        if message.get_content_type() != "multipart/form-data" or not message.is_multipart():
            raise ValueError("Upload must use multipart form data.")
        boundary = message.get_boundary()
        if not boundary or len(boundary) > 200:
            raise ValueError("Upload format is invalid.")
        parts = list(message.iter_parts())
        files = [part for part in parts if part.get_content_disposition() == "form-data"
                 and part.get_param("name", header="content-disposition") == "image"
                 and part.get_filename() is not None]
        if len(parts) != 1 or len(files) != 1:
            raise ValueError("Choose one image file.")
        image = files[0].get_payload(decode=True) or b""
        if not image:
            raise ValueError("The selected image is empty.")
        if len(image) > MAX_UPLOAD_BYTES:
            raise ValueError("Image must be 2 MB or smaller.")
        if image.startswith(b"\xff\xd8\xff") and image.endswith(b"\xff\xd9"):
            extension = "jpg"
        elif image.startswith(b"\x89PNG\r\n\x1a\n") and image[12:16] == b"IHDR":
            extension = "png"
        elif (image[:4] == b"RIFF" and image[8:12] == b"WEBP"
              and image[12:16] in (b"VP8 ", b"VP8L", b"VP8X")):
            extension = "webp"
        else:
            raise ValueError("Choose a valid JPG, PNG or WebP image.")
        return image, extension

    def current_user(self, con):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie.get("nb_session")
        except Exception:
            token = None
        if not token:
            return None
        hashed = hashlib.sha256(token.value.encode()).hexdigest()
        row = con.execute("""SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id
                            WHERE sessions.token_hash=? AND sessions.expires>?""", (hashed, now_iso())).fetchone()
        return row

    def require_admin(self, con):
        user = self.current_user(con)
        if not user or user["role"] != "admin":
            self.respond({"error": "Admin sign-in is required."}, HTTPStatus.UNAUTHORIZED)
            return None
        return user

    def set_session(self, con, user_id: int):
        token = secrets.token_urlsafe(36)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
        con.execute("INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)", (token_hash, user_id, expires))
        cookie = "nb_session=" + token + "; Path=/; HttpOnly; SameSite=Lax; Max-Age=" + str(SESSION_DAYS * 86400)
        if os.environ.get("NEXTBUY_COOKIE_SECURE") == "1":
            cookie += "; Secure"
        return cookie

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/uploads/"):
            return self.serve_upload(path)
        try:
            with db() as con:
                if path == "/api/health":
                    return self.respond({"ok": True, "store": "NextBuy.pk", "database": "SQLite"})
                if path == "/api/catalog":
                    rows = con.execute("SELECT * FROM products ORDER BY id").fetchall()
                    return self.respond({"products": [public_product(p) for p in rows]})
                if path == "/api/store/data":
                    settings = public_settings(con)
                    cats = con.execute("SELECT name,icon,sort_order FROM categories ORDER BY sort_order,name").fetchall()
                    banners = con.execute("SELECT * FROM banners WHERE enabled=1 ORDER BY sort_order,id").fetchall()
                    return self.respond({"categories": [dict(r) for r in cats],
                                         "banners": [public_banner(r) for r in banners], "settings": settings})
                if path.startswith("/api/products/") and path.endswith("/reviews"):
                    product_id = safe_int(path[len("/api/products/"):-len("/reviews")].strip("/"), "Product", 1, 2_000_000_000)
                    rows = con.execute("""SELECT r.id,r.rating,r.text,r.created,u.name FROM reviews r
                                       JOIN users u ON u.id=r.user_id WHERE r.product_id=? AND r.visible=1
                                       ORDER BY r.id DESC""", (product_id,)).fetchall()
                    return self.respond({"reviews": [dict(r) for r in rows]})
                if path == "/api/admin/orders.csv":
                    user = self.require_admin(con)
                    if not user: return
                    query = parse_qs(urlsplit(self.path).query)
                    start, end = (query.get("from", [""])[0], query.get("to", [""])[0])
                    validate_date_filter(start, "Start date")
                    validate_date_filter(end, "End date")
                    sql = "SELECT * FROM orders WHERE 1=1"
                    params = []
                    if start: sql += " AND substr(created,1,10)>=?"; params.append(start)
                    if end: sql += " AND substr(created,1,10)<=?"; params.append(end)
                    sql += " ORDER BY id DESC"
                    output = io.StringIO(newline="")
                    writer = csv.writer(output)
                    writer.writerow(["Order", "Date", "Customer", "Email", "Phone", "City", "Address", "Items", "Subtotal", "Discount", "Coupon", "Shipping", "Total", "Payment", "Payment status", "Order status"])
                    for row in con.execute(sql, params):
                        order = public_order(row)
                        items = "; ".join("%s x %s" % (it["name"], it["qty"]) for it in order["items"])
                        writer.writerow([csv_cell(order["id"]), order["date"], csv_cell(order["customer"]), csv_cell(order["email"]), csv_cell(order["phone"]), csv_cell(order["city"]), csv_cell(order["address"]), csv_cell(items), order["subtotal"], order["discount"], csv_cell(order["couponCode"]), order["shipping"], order["total"], order["payment"], order["paymentStatus"], order["status"]])
                    body = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
                    return self.respond_bytes(body, "text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=nextbuy-orders.csv"})
                if path == "/api/admin/reports":
                    user = self.require_admin(con)
                    if not user: return
                    query = parse_qs(urlsplit(self.path).query)
                    start, end = query.get("from", [""])[0], query.get("to", [""])[0]
                    period = query.get("period", ["daily"])[0]
                    validate_date_filter(start, "Start date")
                    validate_date_filter(end, "End date")
                    if period not in ("daily", "weekly", "monthly"): period = "daily"
                    rows = report_data(con, start, end, period)
                    return self.respond(rows)
                if path.startswith("/api/admin/users/") and path.endswith("/history"):
                    user = self.require_admin(con)
                    if not user: return
                    uid = safe_int(path[len("/api/admin/users/"):-len("/history")].strip("/"), "Customer", 1, 2_000_000_000)
                    customer = con.execute("SELECT * FROM users WHERE id=? AND role='customer'", (uid,)).fetchone()
                    if not customer: return self.respond({"error": "Customer not found."}, HTTPStatus.NOT_FOUND)
                    history = con.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
                    delivered = [r for r in history if r["status"] == "Delivered"]
                    return self.respond({"user": public_user(customer), "orders": [public_order(r) for r in history],
                                         "totalSpent": sum(r["total"] for r in delivered),
                                         "lastOrderDate": history[0]["created"] if history else ""})
                if path.startswith("/api/admin/invoice/"):
                    user = self.require_admin(con)
                    if not user: return
                    code = path[len("/api/admin/invoice/"):][:40]
                    row = con.execute("SELECT * FROM orders WHERE order_code=?", (code,)).fetchone()
                    if not row: return self.respond({"error": "Order not found."}, HTTPStatus.NOT_FOUND)
                    return self.respond_html(invoice_html(public_order(row)))
                if path == "/api/me":
                    return self.respond({"user": public_user(self.current_user(con))})
                if path == "/api/orders":
                    user = self.current_user(con)
                    if not user:
                        return self.respond({"error": "Sign in to view your orders."}, HTTPStatus.UNAUTHORIZED)
                    rows = con.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
                    return self.respond({"orders": [public_order(r) for r in rows]})
                if path == "/api/admin/data":
                    user = self.require_admin(con)
                    if not user: return
                    prows = con.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
                    orows = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
                    urows = con.execute("SELECT * FROM users WHERE role='customer' ORDER BY id DESC").fetchall()
                    delivered = con.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status='Delivered'").fetchone()[0]
                    categories = con.execute("SELECT name,icon,sort_order FROM categories ORDER BY sort_order,name").fetchall()
                    coupons = con.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
                    banners = con.execute("SELECT * FROM banners ORDER BY sort_order,id").fetchall()
                    reviews = con.execute("""SELECT r.*,p.name AS product_name,u.name AS customer_name
                                           FROM reviews r JOIN products p ON p.id=r.product_id
                                           JOIN users u ON u.id=r.user_id ORDER BY r.id DESC""").fetchall()
                    limit = safe_int(setting_value(con, "low_stock_limit", "5"), "Low stock limit", 0, 1_000_000)
                    low_stock = con.execute("SELECT id,name,stock,image FROM products WHERE stock<=? ORDER BY stock,name", (limit,)).fetchall()
                    return self.respond({"products": [public_product(p) for p in prows],
                                         "orders": [public_order(o) for o in orows],
                                         "users": [public_user(u) for u in urows],
                                         "categories": [dict(r) for r in categories],
                                         "coupons": [public_coupon(r) for r in coupons],
                                         "banners": [public_banner(r) for r in banners],
                                         "reviews": [dict(r) for r in reviews],
                                         "settings": public_settings(con),
                                         "lowStock": [dict(r) for r in low_stock],
                                         "stats": {"totalOrders": len(orows), "totalSales": delivered,
                                                   "totalProducts": len(prows), "totalUsers": len(urows)}})
            return self.serve_static(path)
        except Exception as exc:
            return self.respond({"error": "Server error: " + str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        try:
            if path in ("/api/admin/product-image", "/api/admin/banner-image"):
                with db() as con:
                    user = self.require_admin(con)
                    if not user:
                        return
                image, extension = self.read_image_upload()
                UPLOAD_DIR.mkdir(exist_ok=True)
                filename = secrets.token_hex(16) + "." + extension
                image_path = UPLOAD_DIR / filename
                with image_path.open("xb") as uploaded:
                    uploaded.write(image)
                return self.respond({"image": "/uploads/" + filename}, HTTPStatus.CREATED)
            if path.startswith("/api/payments/callback/"):
                raw_type = self.headers.get("Content-Type", "")
                callback = self.read_form() if "application/x-www-form-urlencoded" in raw_type else self.read_json()
                provider = path.rsplit("/", 1)[-1]
                if provider == "easypaisa" and callback.get("auth_token") and not callback.get("status"):
                    import payments
                    order_code = parse_qs(urlsplit(self.path).query).get("order", [""])[0]
                    confirm = os.environ.get("NEXTBUY_EASYPAISA_CONFIRM_URL", "https://easypaystg.easypaisa.com.pk/easypay/confirm.jsf")
                    if not confirm.startswith("https://"):
                        raise ValueError("Easypaisa confirmation URL must use HTTPS.")
                    callback_url = BASE_URL + "/api/payments/callback/easypaisa?order=" + order_code
                    body = "<form id='p' method='post' action='%s'><input type='hidden' name='auth_token' value='%s'><input type='hidden' name='postBackURL' value='%s'></form><script>document.getElementById('p').submit()</script>" % (html_escape(confirm, quote=True), html_escape(callback["auth_token"], quote=True), html_escape(callback_url, quote=True))
                    return self.respond_html("<!doctype html><html><body>Redirecting to Easypaisa..." + body + "</body></html>")
                order_code = str(callback.get("pp_TxnRefNo", callback.get("orderRefNum", callback.get("orderRefNumber", ""))))
                if not order_code:
                    order_code = parse_qs(urlsplit(self.path).query).get("order", [""])[0]
                import payments
                with db() as con:
                    row = con.execute("SELECT * FROM orders WHERE order_code=?", (order_code,)).fetchone()
                    if not row:
                        return self.respond_html("<!doctype html><title>Payment not found</title><p>Order not found.</p>", HTTPStatus.NOT_FOUND)
                    order = public_order(row)
                    if row["payment_provider"] != provider:
                        return self.respond_html("<!doctype html><title>Payment error</title><p>Payment provider mismatch.</p>", HTTPStatus.BAD_REQUEST)
                    if row["payment_status"] == "Paid":
                        return self.respond_html("<!doctype html><title>Payment confirmed</title><p>Payment already confirmed for " + html_escape(order_code) + ".</p><a href='" + html_escape(BASE_URL, quote=True) + "'>Return to store</a>")
                    if row["status"] != "Pending payment":
                        return self.respond_html("<!doctype html><title>Payment not accepted</title><p>This order is no longer awaiting payment.</p><a href='" + html_escape(BASE_URL, quote=True) + "'>Return to store</a>", HTTPStatus.CONFLICT)
                    verified, reference = payments.verify_callback(provider, callback, order)
                    if verified:
                        changed = con.execute("UPDATE orders SET payment_status='Paid',status='Pending',payment_ref=? WHERE id=? AND status='Pending payment' AND payment_status!='Paid'", (reference, row["id"])).rowcount
                        if not changed:
                            latest = con.execute("SELECT * FROM orders WHERE id=?", (row["id"],)).fetchone()
                            if latest and latest["payment_status"] == "Paid":
                                return self.respond_html("<!doctype html><title>Payment confirmed</title><p>Payment already confirmed for " + html_escape(order_code) + ".</p><a href='" + html_escape(BASE_URL, quote=True) + "'>Return to store</a>")
                            return self.respond_html("<!doctype html><title>Payment not accepted</title><p>This order is no longer awaiting payment.</p><a href='" + html_escape(BASE_URL, quote=True) + "'>Return to store</a>", HTTPStatus.CONFLICT)
                        row = con.execute("SELECT * FROM orders WHERE id=?", (row["id"],)).fetchone()
                        order = public_order(row)
                if verified:
                    notify_order(order, "Your payment has been verified.")
                    return self.respond_html("<!doctype html><meta name='viewport' content='width=device-width'><title>Payment confirmed</title><h1>Payment confirmed</h1><p>Order " + html_escape(order_code) + " is now pending fulfilment.</p><a href='" + html_escape(BASE_URL, quote=True) + "'>Return to store</a>")
                return self.respond_html("<!doctype html><meta name='viewport' content='width=device-width'><title>Payment pending</title><h1>Payment not confirmed</h1><p>" + html_escape(reference) + "</p><p>Order " + html_escape(order_code) + " remains pending payment.</p><a href='" + html_escape(BASE_URL, quote=True) + "'>Return to store</a>", HTTPStatus.BAD_REQUEST)
            data = self.read_json()
            with db() as con:
                if path == "/api/bootstrap":
                    if con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
                        items = data.get("products", [])
                        if not isinstance(items, list): items = []
                        for item in items[:100]:
                            try:
                                p = clean_product(item)
                                pid = safe_int(item.get("id"), "Product ID", 1, 2_000_000_000)
                                con.execute("""INSERT OR IGNORE INTO products(id,name,category,price,old_price,image,stock,rating,reviews,description,colors_json,sizes_json,created)
                                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                            (pid,p["name"],p["category"],p["price"],p["old_price"],p["image"],p["stock"],0,0,p["description"],p["colors_json"],p["sizes_json"],now_iso()))
                            except ValueError:
                                continue
                    rows = con.execute("SELECT * FROM products ORDER BY id").fetchall()
                    return self.respond({"products": [public_product(p) for p in rows]})
                if path == "/api/auth/signup":
                    name = str(data.get("name", "")).strip()[:80]
                    email = str(data.get("email", "")).strip().lower()[:180]
                    phone = "".join(ch for ch in str(data.get("phone", "")) if ch.isdigit())[:20] or None
                    password = str(data.get("password", ""))
                    if not name or "@" not in email or len(password) < 8:
                        return self.respond({"error": "Enter your name, valid email and a password with at least 8 characters."}, HTTPStatus.BAD_REQUEST)
                    try:
                        cur = con.execute("INSERT INTO users(name,email,phone,password_hash,role,joined) VALUES(?,?,?,?,?,?)",
                                          (name,email,phone,password_hash(password),"customer",now_iso()))
                    except sqlite3.IntegrityError:
                        return self.respond({"error": "An account with that email or phone already exists."}, HTTPStatus.CONFLICT)
                    row = con.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
                    return self.respond({"user": public_user(row)}, headers={"Set-Cookie": self.set_session(con, row["id"])})
                if path in ("/api/auth/login", "/api/admin/login"):
                    email = str(data.get("email", "")).strip().lower()
                    password = str(data.get("password", ""))
                    row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                    wants_admin = path == "/api/admin/login"
                    lock_key = self.client_address[0] + "|" + email
                    if login_locked(lock_key):
                        return self.respond({"error": "Too many failed attempts. Try again in a few minutes."}, HTTPStatus.TOO_MANY_REQUESTS)
                    if not row or not password_matches(password, row["password_hash"] or "") or (wants_admin and row["role"] != "admin") or (not wants_admin and row["role"] == "admin"):
                        _failed_logins.setdefault(lock_key, []).append(time.time())
                        return self.respond({"error": "Email or password is incorrect."}, HTTPStatus.UNAUTHORIZED)
                    _failed_logins.pop(lock_key, None)
                    return self.respond({"user": public_user(row)}, headers={"Set-Cookie": self.set_session(con, row["id"])})
                if path == "/api/auth/phone/start":
                    phone = "".join(ch for ch in str(data.get("phone", "")) if ch.isdigit())[:20]
                    if len(phone) < 10:
                        return self.respond({"error": "Enter a valid phone number."}, HTTPStatus.BAD_REQUEST)
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("DELETE FROM phone_otp_requests WHERE requested<?", ((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),))
                    request_count = con.execute("SELECT COUNT(*) FROM phone_otp_requests WHERE phone=? AND requested>?", (phone,(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())).fetchone()[0]
                    if request_count >= 3:
                        return self.respond({"error": "OTP request limit reached. Try again in an hour."}, HTTPStatus.TOO_MANY_REQUESTS)
                    code = "%06d" % secrets.randbelow(1_000_000)
                    con.execute("INSERT INTO phone_otp_requests(phone,requested) VALUES(?,?)", (phone,now_iso()))
                    con.commit()
                    try:
                        send_sms_code(phone, code)
                    except Exception as exc:
                        print("SMS delivery failed: " + str(exc), file=sys.stderr)
                        return self.respond({"error": "The verification code could not be sent. Try again later."}, HTTPStatus.SERVICE_UNAVAILABLE)
                    expires = (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
                    con.execute("INSERT OR REPLACE INTO phone_otps(phone,code_hash,expires,attempts,created) VALUES(?,?,?,?,?)",
                                (phone,hashlib.sha256(code.encode()).hexdigest(),expires,0,now_iso()))
                    return self.respond({"sent": True, "message": "If the number can receive SMS, your verification code has been sent."})
                if path == "/api/auth/phone/verify":
                    phone = "".join(ch for ch in str(data.get("phone", "")) if ch.isdigit())[:20]
                    code = str(data.get("code", "")).strip()
                    otp = con.execute("SELECT * FROM phone_otps WHERE phone=?", (phone,)).fetchone()
                    if not otp or otp["expires"] < now_iso():
                        con.execute("DELETE FROM phone_otps WHERE phone=?", (phone,))
                        return self.respond({"error": "The OTP is invalid or expired."}, HTTPStatus.UNAUTHORIZED)
                    if otp["attempts"] >= 5:
                        con.execute("DELETE FROM phone_otps WHERE phone=?", (phone,))
                        return self.respond({"error": "Too many incorrect codes. Request a new OTP."}, HTTPStatus.TOO_MANY_REQUESTS)
                    if not hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), otp["code_hash"]):
                        attempts = otp["attempts"] + 1
                        con.execute("UPDATE phone_otps SET attempts=? WHERE phone=?", (attempts, phone))
                        return self.respond({"error": "The OTP is invalid or expired."}, HTTPStatus.UNAUTHORIZED)
                    con.execute("DELETE FROM phone_otps WHERE phone=?", (phone,))
                    row = con.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
                    if not row:
                        cur = con.execute("INSERT INTO users(name,phone,role,joined) VALUES(?,?,?,?)", ("Customer",phone,"customer",now_iso()))
                        row = con.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
                    return self.respond({"user": public_user(row)}, headers={"Set-Cookie": self.set_session(con, row["id"])})
                if path == "/api/logout":
                    user = self.current_user(con)
                    if user:
                        cookie = SimpleCookie(); cookie.load(self.headers.get("Cookie", ""))
                        con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(cookie["nb_session"].value.encode()).hexdigest(),))
                    cleared = "nb_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
                    if os.environ.get("NEXTBUY_COOKIE_SECURE") == "1":
                        cleared += "; Secure"
                    return self.respond({"ok": True}, headers={"Set-Cookie": cleared})
                if path == "/api/orders/track":
                    code = str(data.get("code", "")).strip().upper()
                    digits = "".join(c for c in str(data.get("phone", "")) if c.isdigit())
                    key = "track|" + self.client_address[0]
                    if login_locked(key):
                        return self.respond({"error": "Too many attempts. Try again in a few minutes."}, HTTPStatus.TOO_MANY_REQUESTS)
                    row = con.execute("SELECT * FROM orders WHERE order_code=?", (code,)).fetchone()
                    stored = "".join(c for c in (row["phone"] if row else "") if c.isdigit())
                    if not row or len(digits) < 10 or stored[-10:] != digits[-10:]:
                        _failed_logins.setdefault(key, []).append(time.time())
                        return self.respond({"error": "No order found with that number and phone."}, HTTPStatus.NOT_FOUND)
                    order = public_order(row)
                    for k in ("userId", "email", "address"):
                        order.pop(k, None)
                    return self.respond({"order": order})
                if path == "/api/auth/password/forgot":
                    email = str(data.get("email", "")).strip().lower()[:180]
                    row = con.execute("SELECT * FROM users WHERE email=? AND role='customer'", (email,)).fetchone()
                    if row:
                        token = secrets.token_urlsafe(32)
                        token_hash = hashlib.sha256(token.encode()).hexdigest()
                        expires = (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
                        con.execute("DELETE FROM password_resets WHERE user_id=?", (row["id"],))
                        con.execute("INSERT INTO password_resets(token_hash,user_id,expires) VALUES(?,?,?)", (token_hash,row["id"],expires))
                        link = BASE_URL + "/#reset/" + token
                        if not send_email(email, "NextBuy.pk password reset", "Use this one-time link within 30 minutes:\n\n" + link):
                            if not os.environ.get("NEXTBUY_SMTP_HOST", "").strip():
                                print("Password reset link for %s: %s" % (email, link))
                    return self.respond({"message": "If an account exists for that email, a reset link has been sent."})
                if path == "/api/auth/password/reset":
                    token = str(data.get("token", ""))[:200]
                    password = str(data.get("password", ""))
                    if len(password) < 8:
                        return self.respond({"error": "Password must contain at least 8 characters."}, HTTPStatus.BAD_REQUEST)
                    hashed_token = hashlib.sha256(token.encode()).hexdigest()
                    reset = con.execute("SELECT * FROM password_resets WHERE token_hash=? AND used=0 AND expires>?", (hashed_token,now_iso())).fetchone()
                    if not reset:
                        return self.respond({"error": "Reset link is invalid or expired."}, HTTPStatus.BAD_REQUEST)
                    con.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(password),reset["user_id"]))
                    con.execute("DELETE FROM password_resets WHERE user_id=?", (reset["user_id"],))
                    return self.respond({"ok": True})
                if path == "/api/coupons/validate":
                    quote = quote_order(con, data.get("items", []), str(data.get("city", ""))[:100], data.get("code", ""))
                    return self.respond({"subtotal": quote["subtotal"], "discount": quote["discount"],
                                         "couponCode": quote["coupon_code"], "shipping": quote["shipping"], "total": quote["total"]})
                if path == "/api/reviews":
                    user = self.current_user(con)
                    if not user or user["role"] != "customer":
                        return self.respond({"error": "Sign in with your customer account to review products."}, HTTPStatus.UNAUTHORIZED)
                    product_id = safe_int(data.get("productId"), "Product", 1, 2_000_000_000)
                    rating = safe_int(data.get("rating"), "Rating", 1, 5)
                    text = str(data.get("text", "")).strip()[:2000]
                    if not text:
                        return self.respond({"error": "Write a short review before submitting."}, HTTPStatus.BAD_REQUEST)
                    if not con.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
                        return self.respond({"error": "Product not found."}, HTTPStatus.NOT_FOUND)
                    bought = False
                    for purchase in con.execute("SELECT items_json FROM orders WHERE user_id=? AND status!='Cancelled'", (user["id"],)):
                        if any(int(item.get("id", 0)) == product_id for item in json.loads(purchase["items_json"])):
                            bought = True; break
                    if not bought:
                        return self.respond({"error": "You can review a product after ordering it."}, HTTPStatus.FORBIDDEN)
                    try:
                        con.execute("INSERT INTO reviews(product_id,user_id,rating,text,visible,created) VALUES(?,?,?,?,1,?)", (product_id,user["id"],rating,text,now_iso()))
                    except sqlite3.IntegrityError:
                        return self.respond({"error": "You have already reviewed this product."}, HTTPStatus.CONFLICT)
                    recalculate_product_rating(con, product_id)
                    return self.respond({"ok": True})
                if path == "/api/admin/categories":
                    if not self.require_admin(con): return
                    name, icon = validate_category(data.get("name"), data.get("icon"))
                    sort_order = safe_int(data.get("sortOrder", 100), "Sort order", 0, 1_000_000)
                    try: con.execute("INSERT INTO categories(name,icon,sort_order) VALUES(?,?,?)", (name,icon,sort_order))
                    except sqlite3.IntegrityError: return self.respond({"error": "That category already exists."}, HTTPStatus.CONFLICT)
                    return self.respond({"name": name, "icon": icon}, HTTPStatus.CREATED)
                if path == "/api/admin/coupons":
                    if not self.require_admin(con): return
                    coupon = validate_coupon(data)
                    try:
                        con.execute("INSERT INTO coupons(code,kind,amount,min_order,expires,usage_limit,enabled,created) VALUES(?,?,?,?,?,?,?,?)",
                                    (coupon["code"],coupon["kind"],coupon["amount"],coupon["min_order"],coupon["expires"],coupon["usage_limit"],coupon["enabled"],now_iso()))
                    except sqlite3.IntegrityError: return self.respond({"error": "That coupon code already exists."}, HTTPStatus.CONFLICT)
                    return self.respond({"ok": True}, HTTPStatus.CREATED)
                if path == "/api/admin/banners":
                    if not self.require_admin(con): return
                    image = str(data.get("image", "")).strip()[:1500]
                    headline = str(data.get("headline", "")).strip()[:200]
                    label = str(data.get("buttonLabel", "Shop Now")).strip()[:50]
                    link = str(data.get("buttonLink", "#products")).strip()[:500]
                    if not image.startswith(("/uploads/", "https://", "http://")) or not headline or not label or not link.startswith(("#", "/", "https://")):
                        raise ValueError("Banner image, headline and a safe button link are required.")
                    order = safe_int(data.get("sortOrder", 100), "Sort order", 0, 1_000_000)
                    con.execute("INSERT INTO banners(image,headline,button_label,button_link,sort_order,enabled) VALUES(?,?,?,?,?,1)", (image,headline,label,link,order))
                    return self.respond({"ok": True}, HTTPStatus.CREATED)
                if path == "/api/admin/banners/reorder":
                    if not self.require_admin(con): return
                    ids = data.get("ids", [])
                    if not isinstance(ids, list) or len(ids) > 50: raise ValueError("Banner order is invalid.")
                    for index, banner_id in enumerate(ids):
                        con.execute("UPDATE banners SET sort_order=? WHERE id=?", (index,safe_int(banner_id,"Banner",1,2_000_000_000)))
                    return self.respond({"ok": True})
                if path == "/api/admin/settings":
                    if not self.require_admin(con): return
                    shipping = safe_int(data.get("shippingDefault",0),"Delivery charge",0,1_000_000)
                    threshold = safe_int(data.get("freeDeliveryAbove",0),"Free delivery threshold",0,100_000_000)
                    low = safe_int(data.get("lowStockLimit",5),"Low stock limit",0,1_000_000)
                    city_charges = data.get("cityCharges",{})
                    if not isinstance(city_charges,dict) or len(city_charges)>100: raise ValueError("City delivery charges are invalid.")
                    cleaned = {}
                    for city, amount in city_charges.items():
                        name = str(city).strip()[:60]
                        if not name: continue
                        cleaned[name] = safe_int(amount,"City delivery charge",0,1_000_000)
                    for key,value in (("shipping_default",shipping),("free_delivery_above",threshold),("low_stock_limit",low),("city_charges",json.dumps(cleaned,ensure_ascii=False))):
                        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
                    return self.respond({"settings": public_settings(con)})
                if path == "/api/admin/reviews":
                    if not self.require_admin(con): return
                    return self.respond({"ok": True})
                if path == "/api/orders":
                    user = self.current_user(con)
                    customer = str(data.get("customer", "")).strip()
                    if not customer:
                        return self.respond({"error": "Enter the customer name."}, HTTPStatus.BAD_REQUEST)
                    payment = str(data.get("payment", "Cash on Delivery")).lower()
                    provider = "jazzcash" if "jazzcash" in payment else "easypaisa" if "easypaisa" in payment else ""
                    if provider:
                        import payments
                        if not payments.configured(provider):
                            return self.respond({"error": "Online payment is not configured yet. Choose Cash on Delivery or configure the provider credentials."}, HTTPStatus.SERVICE_UNAVAILABLE)
                    con.execute("BEGIN IMMEDIATE")
                    payment_request = None
                    try:
                        order = create_order(con, data, user["id"] if user and user["role"] == "customer" else None, "Online", "Pending")
                        if provider:
                            payment_request = payments.create_request(provider, order, BASE_URL)
                        con.commit()
                    except Exception:
                        con.rollback(); raise
                    notify_order(order, "Your order has been placed.")
                    result = {"order": order, "products": [public_product(p) for p in con.execute("SELECT * FROM products ORDER BY id").fetchall()]}
                    if payment_request:
                        result["paymentRequest"] = payment_request
                    return self.respond(result, HTTPStatus.CREATED)
                if path == "/api/admin/products":
                    user = self.require_admin(con)
                    if not user: return
                    p = clean_product(data)
                    if not con.execute("SELECT 1 FROM categories WHERE name=?", (p["category"],)).fetchone():
                        return self.respond({"error": "Choose a category managed in the admin page."}, HTTPStatus.BAD_REQUEST)
                    con.execute("""INSERT INTO products(name,category,price,old_price,image,stock,rating,reviews,description,colors_json,sizes_json,created,gallery_json)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (p["name"],p["category"],p["price"],p["old_price"],p["image"],p["stock"],0,0,p["description"],p["colors_json"],p["sizes_json"],now_iso(),p["gallery_json"]))
                    product_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                    return self.respond({"product": public_product(con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())}, HTTPStatus.CREATED)
                if path == "/api/admin/pos":
                    user = self.require_admin(con)
                    if not user: return
                    con.execute("BEGIN IMMEDIATE")
                    try:
                        order = create_order(con, data, None, "POS", "Delivered")
                        con.commit()
                    except Exception:
                        con.rollback(); raise
                    return self.respond({"order": order, "products": [public_product(p) for p in con.execute("SELECT * FROM products ORDER BY id").fetchall()]}, HTTPStatus.CREATED)
            return self.respond({"error": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.respond({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self.respond({"error": "Server error: " + str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self):
        path = unquote(urlsplit(self.path).path)
        try:
            with db() as con:
                user = self.require_admin(con)
                if not user: return
                data = self.read_json()
                prefix = "/api/admin/products/"
                if path.startswith(prefix):
                    pid = safe_int(path[len(prefix):], "Product ID", 1, 2_000_000_000)
                    p = clean_product(data)
                    if not con.execute("SELECT 1 FROM categories WHERE name=?", (p["category"],)).fetchone():
                        return self.respond({"error": "Choose a category managed in the admin page."}, HTTPStatus.BAD_REQUEST)
                    cur = con.execute("""UPDATE products SET name=?,category=?,price=?,old_price=?,image=?,stock=?,description=?,colors_json=?,sizes_json=?,gallery_json=? WHERE id=?""",
                                      (p["name"],p["category"],p["price"],p["old_price"],p["image"],p["stock"],p["description"],p["colors_json"],p["sizes_json"],p["gallery_json"],pid))
                    if not cur.rowcount: return self.respond({"error": "Product not found."}, HTTPStatus.NOT_FOUND)
                    recalculate_product_rating(con, pid)
                    return self.respond({"product": public_product(con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())})
                prefix = "/api/admin/categories/"
                if path.startswith(prefix):
                    old_name = path[len(prefix):][:60]
                    row = con.execute("SELECT * FROM categories WHERE name=?", (old_name,)).fetchone()
                    if not row: return self.respond({"error": "Category not found."}, HTTPStatus.NOT_FOUND)
                    name, icon = validate_category(data.get("name"), data.get("icon"))
                    sort_order = safe_int(data.get("sortOrder",row["sort_order"]),"Sort order",0,1_000_000)
                    try:
                        con.execute("UPDATE categories SET name=?,icon=?,sort_order=? WHERE name=?",(name,icon,sort_order,old_name))
                        con.execute("UPDATE products SET category=? WHERE category=?",(name,old_name))
                    except sqlite3.IntegrityError:
                        return self.respond({"error": "That category already exists."}, HTTPStatus.CONFLICT)
                    return self.respond({"ok": True})
                prefix = "/api/admin/coupons/"
                if path.startswith(prefix):
                    cid = safe_int(path[len(prefix):],"Coupon",1,2_000_000_000)
                    c = validate_coupon(data)
                    cur = con.execute("UPDATE coupons SET code=?,kind=?,amount=?,min_order=?,expires=?,usage_limit=?,enabled=? WHERE id=?",
                                      (c["code"],c["kind"],c["amount"],c["min_order"],c["expires"],c["usage_limit"],c["enabled"],cid))
                    if not cur.rowcount: return self.respond({"error": "Coupon not found."}, HTTPStatus.NOT_FOUND)
                    return self.respond({"ok": True})
                prefix = "/api/admin/banners/"
                if path.startswith(prefix):
                    bid = safe_int(path[len(prefix):],"Banner",1,2_000_000_000)
                    image = str(data.get("image", "")).strip()[:1500]
                    headline = str(data.get("headline", "")).strip()[:200]
                    label = str(data.get("buttonLabel", "Shop Now")).strip()[:50]
                    link = str(data.get("buttonLink", "#products")).strip()[:500]
                    order = safe_int(data.get("sortOrder",100),"Sort order",0,1_000_000)
                    if not image.startswith(("/uploads/","https://","http://")) or not headline or not label or not link.startswith(("#","/","https://")):
                        raise ValueError("Banner image, headline and a safe button link are required.")
                    cur = con.execute("UPDATE banners SET image=?,headline=?,button_label=?,button_link=?,sort_order=?,enabled=? WHERE id=?",
                                      (image,headline,label,link,order,1 if data.get("enabled",True) else 0,bid))
                    if not cur.rowcount: return self.respond({"error": "Banner not found."}, HTTPStatus.NOT_FOUND)
                    return self.respond({"ok": True})
            return self.respond({"error": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.respond({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except sqlite3.IntegrityError:
            return self.respond({"error": "That value already exists."}, HTTPStatus.CONFLICT)
        except Exception as exc:
            return self.respond({"error": "Server error: " + str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self):
        path = unquote(urlsplit(self.path).path)
        try:
            with db() as con:
                user = self.require_admin(con)
                if not user: return
                if path.startswith("/api/admin/products/"):
                    pid = safe_int(path[len("/api/admin/products/"):], "Product ID", 1, 2_000_000_000)
                    cur = con.execute("DELETE FROM products WHERE id=?", (pid,))
                    if not cur.rowcount: return self.respond({"error": "Product not found."}, HTTPStatus.NOT_FOUND)
                    return self.respond({"ok": True})
                if path.startswith("/api/admin/categories/"):
                    name = path[len("/api/admin/categories/"):][:60]
                    if con.execute("SELECT 1 FROM products WHERE category=?", (name,)).fetchone():
                        return self.respond({"error": "Move or delete this category's products before removing it."}, HTTPStatus.CONFLICT)
                    cur = con.execute("DELETE FROM categories WHERE name=?", (name,))
                    if not cur.rowcount: return self.respond({"error": "Category not found."}, HTTPStatus.NOT_FOUND)
                    return self.respond({"ok": True})
                for prefix, table in (("/api/admin/coupons/", "coupons"), ("/api/admin/banners/", "banners"), ("/api/admin/reviews/", "reviews")):
                    if path.startswith(prefix):
                        record_id = safe_int(path[len(prefix):], table[:-1].title(), 1, 2_000_000_000)
                        row = con.execute("SELECT product_id FROM reviews WHERE id=?",(record_id,)).fetchone() if table == "reviews" else None
                        cur = con.execute("DELETE FROM " + table + " WHERE id=?",(record_id,))
                        if not cur.rowcount: return self.respond({"error": "Record not found."}, HTTPStatus.NOT_FOUND)
                        if row: recalculate_product_rating(con,row["product_id"])
                        return self.respond({"ok": True})
            return self.respond({"error": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.respond({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self.respond({"error": "Server error: " + str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self):
        path = unquote(urlsplit(self.path).path)
        try:
            data = self.read_json()
            with db() as con:
                user = self.require_admin(con)
                if not user: return
                if path.startswith("/api/admin/reviews/"):
                    review_id = safe_int(path[len("/api/admin/reviews/"):],"Review",1,2_000_000_000)
                    visible = 1 if data.get("visible") else 0
                    cur = con.execute("UPDATE reviews SET visible=? WHERE id=?",(visible,review_id))
                    if not cur.rowcount: return self.respond({"error": "Review not found."}, HTTPStatus.NOT_FOUND)
                    review = con.execute("SELECT product_id FROM reviews WHERE id=?",(review_id,)).fetchone()
                    recalculate_product_rating(con,review["product_id"])
                    return self.respond({"ok": True})
                prefix = "/api/admin/orders/"
                if not path.startswith(prefix): return self.respond({"error": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
                code = path[len(prefix):][:40]
                status = str(data.get("status", ""))
                if status not in ("Pending payment", "Pending", "Shipped", "Delivered", "Cancelled"):
                    return self.respond({"error": "Invalid order status."}, HTTPStatus.BAD_REQUEST)
                con.execute("BEGIN IMMEDIATE")
                try:
                    row = con.execute("SELECT * FROM orders WHERE order_code=?", (code,)).fetchone()
                    if not row:
                        con.rollback()
                        return self.respond({"error": "Order not found."}, HTTPStatus.NOT_FOUND)
                    if status in ("Shipped", "Delivered") and row["payment_provider"] and row["payment_status"] != "Paid":
                        con.rollback()
                        return self.respond({"error": "Verify online payment before fulfilment."}, HTTPStatus.CONFLICT)
                    was, items = row["status"], json.loads(row["items_json"])
                    if was != "Cancelled" and status == "Cancelled":
                        for item in items:
                            con.execute("UPDATE products SET stock=stock+? WHERE id=?", (int(item["qty"]), int(item["id"])))
                    elif was == "Cancelled" and status != "Cancelled":
                        for item in items:
                            if con.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=?", (int(item["qty"]), int(item["id"]), int(item["qty"]))).rowcount != 1:
                                con.rollback()
                                return self.respond({"error": "Not enough stock to re-open this order."}, HTTPStatus.BAD_REQUEST)
                    payment_status = "Paid" if status == "Delivered" and not row["payment_provider"] else row["payment_status"]
                    con.execute("UPDATE orders SET status=?,payment_status=? WHERE order_code=?", (status,payment_status,code))
                    con.commit()
                except Exception:
                    con.rollback(); raise
                updated = con.execute("SELECT * FROM orders WHERE order_code=?",(code,)).fetchone()
                order = public_order(updated)
            if was != status:
                notify_order(order, "Your order status is now " + status + ".")
            return self.respond({"ok": True})
        except ValueError as exc:
            return self.respond({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self.respond({"error": "Server error: " + str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_upload(self, path: str):
        prefix = "/uploads/"
        filename = path[len(prefix):]
        if not re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|png|webp)", filename):
            return self.respond({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        try:
            upload_root = UPLOAD_DIR.resolve(strict=True)
            file_path = (UPLOAD_DIR / filename).resolve(strict=True)
            if file_path.parent != upload_root or not file_path.is_file():
                return self.respond({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            size = file_path.stat().st_size
            if size <= 0 or size > MAX_UPLOAD_BYTES:
                return self.respond({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            content = file_path.read_bytes()
        except (OSError, RuntimeError):
            return self.respond({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        extension = filename.rsplit(".", 1)[1]
        content_type = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[extension]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        self.wfile.write(content)

    def serve_static(self, path: str):
        allowed = {"/", "/index.html", "/app.js", "/features.js"}
        if path not in allowed:
            return self.respond({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        file_path = ROOT / ("index.html" if path == "/" else path.lstrip("/"))
        if not file_path.is_file():
            return self.respond({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        content = file_path.read_bytes()
        ctype = "text/javascript; charset=utf-8" if file_path.suffix == ".js" else "text/css; charset=utf-8" if file_path.suffix == ".css" else "text/html; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        self.wfile.write(content)

    def send_error(self, code, message=None, explain=None):
        self.respond({"error": message or "Request failed."}, code)


if __name__ == "__main__":
    init_db()
    print("NextBuy.pk running at http://%s:%s" % (HOST, PORT))
    print("Database: %s" % DB_PATH)
    print("Admin account: %s" % ADMIN_EMAIL)
    print("Phone OTP: %s" % ("demo mode" if os.environ.get("NEXTBUY_DEMO_OTP") == "1" else "SMS provider required"))
    try:
        ThreadingHTTPServer((HOST, PORT), StoreHandler).serve_forever()
    except KeyboardInterrupt:
        print("\nNextBuy server stopped.")

