# NextBuy.pk setup and deployment

The site uses only Python's standard library. Run it with Python 3.10 or later. Keep `server.py`, `payments.py`, `app.js`, `features.js`, `index.html`, `nextbuy.db`, and the `uploads` folder together. Backups are written to `backups/`.

## Local setup and admin password

1. Open PowerShell in the website folder and start the server with `py server.py`, or double-click `start_nextbuy.bat`.
2. For local OTP testing only, set `$env:NEXTBUY_DEMO_OTP="1"` in that PowerShell window before starting the server. The code appears in the server console. Remove the setting or use `0` outside local testing.
3. Change the admin password before publishing the site. Stop the server and run `py set_admin_password.py`. It asks for a new password without echoing it and revokes existing admin sessions. If the database uses a different admin email, set `NEXTBUY_ADMIN_EMAIL` to that existing address before running it.
4. Do not delete or replace an existing `nextbuy.db` when updating the files; startup adds the new columns and tables safely.

## Environment settings

The app reads operating-system environment variables. It does not load a `.env` file. Set only the providers you use, and keep secret values out of the source files and Git.

| Variable | Purpose |
| --- | --- |
| `NEXTBUY_HOST` | Bind address. Use `127.0.0.1` behind Nginx; default is local-only. |
| `NEXTBUY_PORT` | Port, usually `8000`. |
| `NEXTBUY_BASE_URL` | Full public URL, for example `https://shop.example.pk`; used for reset and payment return links. |
| `NEXTBUY_ADMIN_EMAIL` | Admin login email. |
| `NEXTBUY_ADMIN_PASSWORD` | Initial password only when the admin account is first created. For an existing database, use `set_admin_password.py`. |
| `NEXTBUY_COOKIE_SECURE` | Set to `1` when HTTPS is enabled. |
| `NEXTBUY_WHATSAPP_NUMBER` | WhatsApp number in international digits, for example `923001234567`, without `+` or spaces. |
| `NEXTBUY_SMTP_HOST`, `NEXTBUY_SMTP_PORT`, `NEXTBUY_SMTP_USER`, `NEXTBUY_SMTP_PASSWORD`, `NEXTBUY_SMTP_FROM`, `NEXTBUY_SMTP_TLS` | Optional email for password resets and order notices. Use port `587` and TLS `1` for a typical STARTTLS account. |
| `NEXTBUY_DEMO_OTP` | Set to `1` only for local testing. Otherwise set `0` and configure the SMS provider. |
| `NEXTBUY_SMS_PROVIDER_URL`, `NEXTBUY_SMS_API_KEY` | HTTPS JSON API URL and key. The adapter sends `to`, `message`, and `code` in JSON, with the key in Bearer and `X-API-Key` headers. Adjust `send_sms_code()` if your provider requires a different request format. |

### JazzCash sandbox

Create a JazzCash merchant account and obtain its sandbox merchant ID, password, and integrity salt. Set:

```text
NEXTBUY_JAZZCASH_MERCHANT_ID=your-sandbox-merchant-id
NEXTBUY_JAZZCASH_PASSWORD=your-sandbox-password
NEXTBUY_JAZZCASH_INTEGRITY_SALT=your-sandbox-integrity-salt
NEXTBUY_JAZZCASH_PAYMENT_URL=https://sandbox.jazzcash.com.pk/CustomerPortal/transactionmanagement/merchantform/
NEXTBUY_JAZZCASH_TXN_TYPE=MWALLET
```

The hosted form is signed by `payments.py`; its callback is accepted only after the HMAC, merchant, order reference, amount, currency, and success response checks pass. Ask JazzCash to enable the hosted checkout and confirm the return URL for your merchant account. Replace the sandbox URL and credentials with the production values only after their sandbox approval.

### Easypaisa staging

Register a merchant account in Easypaisa's staging portal and use the exact integration guide and verification method issued for that account. Set:

```text
NEXTBUY_EASYPAISA_STORE_ID=your-staging-store-id
NEXTBUY_EASYPAISA_PAYMENT_URL=https://easypaystg.easypaisa.com.pk/easypay/Index.jsf
NEXTBUY_EASYPAISA_CONFIRM_URL=https://easypaystg.easypaisa.com.pk/easypay/confirm.jsf
NEXTBUY_EASYPAISA_VERIFY_URL=https://your-merchant-verification-endpoint
```

The hosted flow sends Easypaisa's first `auth_token` callback to its confirmation page, then waits for a verified result before changing an order from `Pending payment`. The verification adapter posts `auth_token`, `orderRefNum`, and `storeId` to `NEXTBUY_EASYPAISA_VERIFY_URL` and expects JSON containing a successful `status`, matching `orderRefNum`, matching `amount`, and optionally `transactionId`. If Easypaisa gives your account a signed callback contract instead, configure `NEXTBUY_EASYPAISA_CALLBACK_SECRET` only when its signature format matches the adapter in `payments.py`. Confirm the precise callback and request-signing rules with Easypaisa; merchant accounts can use different integration versions. Do not mark orders paid from an unverified browser redirect.

When sandbox testing passes, replace the staging URLs and credentials with the production values issued by Easypaisa. Never use live credentials in a browser or commit them to source control.

## Daily database backup

`backup_db.py` uses SQLite's online backup API, so it can run while the server is active. It writes timestamped files to `backups/` and deletes older copies after keeping the newest 14.

### Windows Task Scheduler

1. Find your Python executable with `where.exe python`.
2. Open **Task Scheduler** → **Create Basic Task**. Name it `NextBuy database backup` and choose **Daily** at a quiet time such as 2:00 AM.
3. Choose **Start a program**. Set **Program/script** to the Python path from step 1, **Add arguments** to `"C:\Users\NADEEM\Desktop\website online\backup_db.py"`, and **Start in** to `C:\Users\NADEEM\Desktop\website online`.
4. Save the task. In its properties, enable **Run whether user is logged on or not** if the computer will be unattended. Keep the PC powered on at the scheduled time.

### Ubuntu daily backup

After installing the project at `/opt/nextbuy`, create a daily cron job:

```bash
sudo tee /etc/cron.d/nextbuy-backup >/dev/null <<'EOF'
0 2 * * * nextbuy /usr/bin/python3 /opt/nextbuy/backup_db.py >> /var/log/nextbuy-backup.log 2>&1
EOF
sudo chmod 644 /etc/cron.d/nextbuy-backup
```

The 14 local copies protect against common mistakes, but copy backups off the server regularly so a disk or VPS failure does not remove both the site and its backups.

## Ubuntu VPS with systemd, Nginx, and HTTPS

Use a domain whose DNS `A` record points to the VPS. These commands assume Ubuntu 22.04 or later, a project uploaded to `/opt/nextbuy`, and the public hostname `shop.example.pk`; replace the example domain and email.

1. Install Python, Nginx, and Let's Encrypt tools:

```bash
sudo apt update
sudo apt install -y python3 nginx certbot python3-certbot-nginx
sudo useradd --system --home /opt/nextbuy --shell /usr/sbin/nologin nextbuy
sudo mkdir -p /opt/nextbuy
sudo chown -R nextbuy:nextbuy /opt/nextbuy
```

Upload the project files and the existing `nextbuy.db` plus `uploads/` to `/opt/nextbuy`, then run `sudo chown -R nextbuy:nextbuy /opt/nextbuy` again. Do not upload `.env` or credential files into the public web root.

2. Create a root-readable environment file. Replace every placeholder before continuing:

```bash
sudo nano /etc/nextbuy.env
```

Add at least:

```text
NEXTBUY_HOST=127.0.0.1
NEXTBUY_PORT=8000
NEXTBUY_BASE_URL=https://shop.example.pk
NEXTBUY_ADMIN_EMAIL=admin@shop.example.pk
NEXTBUY_ADMIN_PASSWORD=replace-with-a-long-random-initial-password
NEXTBUY_COOKIE_SECURE=1
NEXTBUY_DEMO_OTP=0
NEXTBUY_WHATSAPP_NUMBER=923001234567
```

Add SMTP, SMS, and payment settings from the sections above if you use those services. Lock down the file:

```bash
sudo chown root:root /etc/nextbuy.env
sudo chmod 600 /etc/nextbuy.env
```

For an existing database, run `sudo -u nextbuy env NEXTBUY_ADMIN_EMAIL=admin@shop.example.pk /usr/bin/python3 /opt/nextbuy/set_admin_password.py` from a protected terminal to replace its current admin password. Replace the example email with the admin email in `/etc/nextbuy.env`. This script prompts securely.

3. Create the systemd service:

```bash
sudo tee /etc/systemd/system/nextbuy.service >/dev/null <<'EOF'
[Unit]
Description=NextBuy.pk Python store
After=network.target

[Service]
Type=simple
User=nextbuy
Group=nextbuy
WorkingDirectory=/opt/nextbuy
EnvironmentFile=/etc/nextbuy.env
ExecStart=/usr/bin/python3 /opt/nextbuy/server.py
Restart=on-failure
RestartSec=3
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now nextbuy
sudo systemctl status nextbuy --no-pager
```

4. Add an Nginx reverse proxy:

```bash
sudo tee /etc/nginx/sites-available/nextbuy >/dev/null <<'EOF'
server {
    listen 80;
    server_name shop.example.pk;
    client_max_body_size 3m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/nextbuy /etc/nginx/sites-enabled/nextbuy
sudo nginx -t
sudo systemctl reload nginx
```

5. Issue the HTTPS certificate and allow web traffic:

```bash
sudo certbot --nginx -d shop.example.pk --redirect -m admin@shop.example.pk --agree-tos --no-eff-email
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo systemctl reload nginx
```

Certbot configures renewal automatically. Verify with `sudo certbot renew --dry-run`. Python stays bound to localhost; only Nginx should be reachable from the internet.

6. Check startup and logs:

```bash
sudo systemctl restart nextbuy
sudo journalctl -u nextbuy -n 100 --no-pager
sudo tail -n 100 /var/log/nginx/error.log
```

## Manual local test checklist

Start with `py server.py` and open `http://localhost:8000`. Use test products and a non-production email/phone while checking these flows:

1. Sign in as admin, add a product using a JPG/PNG/WebP under 2 MB, refresh, then edit it. Try an over-limit or unsupported image and confirm it is rejected.
2. Create a percent and a fixed coupon. Apply one in the cart, then change the cart and confirm the discount recalculates. Disable or expire it and confirm checkout rejects it.
3. In Shipping & Stock, set a default charge, a free-delivery threshold, and a city override. Check cart and checkout totals for each case, then place a COD order and confirm its saved shipping and total in admin.
4. In Orders, set CSV date filters and download the file. Open an order invoice and print it.
5. Set a small low-stock limit and save a product below it. Confirm the dashboard alert and product-row highlight; increase stock and confirm it clears.
6. Add, rename, and delete an unused category. Confirm the storefront filter/navigation and product form use the database list.
7. Upload two banners, change their text and link, move their order, then delete them. Confirm that deleting all banners restores the default slides.
8. Place an order as a customer, open that product detail page, and submit one review. Confirm the rating changes; hide and delete the review in admin.
9. Request a password reset. With SMTP unset, copy the one-time link from the server console; with SMTP configured, use the email. Reset once, then confirm the old link cannot be reused.
10. Open Customers in admin and view a customer profile. Check orders, delivered total spent, and last order date.
11. Mark a test order Delivered and view daily, weekly, and monthly reports with a date range. Cancel another order and confirm it is excluded from sales.
12. Configure SMTP and place an order with a customer email; update its status and confirm both messages arrive. Configure WhatsApp and use the order/support click-to-chat buttons.
13. Switch to اردو, refresh, and confirm the choice is remembered and the layout is right-to-left. Switch back to English.
14. Open a product card's details and confirm the URL has `#product/<id>`. Check gallery thumbnails, options, stock, reviews, and related items.
15. Run `py backup_db.py` while the server is active. Confirm a timestamped copy appears in `backups/`; after creating more than 14 test backups, only the newest 14 should remain.
16. For local-only OTP testing, set `$env:NEXTBUY_DEMO_OTP="1"`, restart the server, request a code, and read it in the server console. Confirm the response does not show it. For a real provider, set its HTTPS URL/key and test delivery; confirm the 5-minute expiry, five wrong attempts, and three requests per phone per hour.
17. Use merchant sandbox credentials and the provider's test account. Confirm the order stays Pending payment until the verified return arrives. Try a failed/altered amount callback and confirm it remains unpaid. Confirm COD orders still complete without a gateway.
18. Follow the VPS steps below, then check HTTPS, secure cookies, admin access, one order, and the first scheduled backup before promoting the domain.
## Before going live checklist

- Change the admin password with `set_admin_password.py`; remove any default or temporary credentials.
- Set `NEXTBUY_BASE_URL` to the HTTPS domain and `NEXTBUY_COOKIE_SECURE=1`.
- Keep `NEXTBUY_HOST=127.0.0.1` behind Nginx and expose only ports 80/443 (plus SSH).
- Set `NEXTBUY_DEMO_OTP=0`; configure and test your SMS provider over HTTPS.
- Configure SMTP if customers need password-reset emails and email order notices.
- Configure a valid international WhatsApp number if you want WhatsApp click-to-chat buttons.
- Use sandbox merchant credentials first. Confirm return URLs, request signatures, callback verification, amount matching, and cancellation behavior with each payment provider before switching to live credentials.
- Schedule the daily backup and verify that a backup file is created. Keep an off-server copy.
- Set real shipping prices and free-delivery threshold in the admin Shipping & Stock page; set the low-stock threshold.
- Add real product images, current stock, category data, and banner copy; remove demo catalog data if it is not for sale.
- Keep all secrets in the service environment, restrict `/etc/nextbuy.env`, and never commit credentials or database backups to a public repository.
