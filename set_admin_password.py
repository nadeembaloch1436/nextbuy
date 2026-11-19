from __future__ import annotations

import getpass
import os
import sys

import server


def main() -> None:
    if not server.DB_PATH.is_file():
        raise SystemExit("Database not found: %s. Start the site once before changing its admin password." % server.DB_PATH)
    email = os.environ.get("NEXTBUY_ADMIN_EMAIL", "admin@nextbuy.pk").strip().lower()
    password = getpass.getpass("New admin password (at least 12 characters): ")
    confirm = getpass.getpass("Confirm new admin password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match.")
    if len(password) < 12:
        raise SystemExit("Use at least 12 characters for the admin password.")

    with server.db() as con:
        admin = con.execute("SELECT id FROM users WHERE email=? AND role='admin'", (email,)).fetchone()
        if not admin:
            raise SystemExit("Admin account not found for %s." % email)
        con.execute("UPDATE users SET password_hash=? WHERE id=?", (server.password_hash(password), admin["id"]))
        con.execute("DELETE FROM sessions WHERE user_id=?", (admin["id"],))
    print("Admin password updated. Sign in again with %s." % email)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Cancelled.")
