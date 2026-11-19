# NextBuy.pk setup

## Start the website

1. Keep this folder on the computer that will run the store.
2. Double-click `start_nextbuy.bat`.
3. In your browser, open `http://localhost:8000`.
4. Keep the black server window open while using the store. Press Ctrl+C in that window to stop it.

Files: `index.html` (store + admin UI), `app.js` (talks to the server), `server.py` (web server + API), `nextbuy.db` (data). The store uses Python's built-in web server and SQLite database; it needs no package installation. Orders, users, products, stock, and POS sales are saved in `nextbuy.db` in this folder. Back up that file to back up store data.

## Admin login

- Email: `admin@nextbuy.pk`
- Password: `admin123`
- Open the Admin button or go to `http://localhost:8000/#admin`. **Change the default password before real use:** stop the server and run `python set_admin_password.py`. After 5 wrong attempts the login is locked for 5 minutes.

For a different initial admin password, set the `NEXTBUY_ADMIN_PASSWORD` environment variable before the first server start. The initial admin account is created when the database is first created.

## Demo and payment details

- Phone sign-in uses demo OTP `1234`; the app does not send a real SMS.
- Email sign-in uses an account email and password; Google OAuth is not connected.
- COD, JazzCash, and Easypaisa are saved as the selected payment method. This build does not contact payment providers.
- Delivery is free nationwide, as configured in the checkout.

## Hosting

This setup runs the frontend and backend on the same computer at localhost. Customers outside that computer cannot reach the store until the folder is deployed to a web host with Python support and HTTPS. Real SMS and online payments need provider accounts and credentials.
