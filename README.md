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

## Netlify

Netlify can publish the storefront files, but it does not run this Python server.py backend. Login, sign-up, orders and admin features need the Python server hosted separately. Configure Netlify to proxy /api/* (and /uploads/*) to that backend, and give the backend persistent storage for the SQLite database and uploaded images. Do not publish nextbuy.db or server secrets as static files.

## Netlify and Render

The Netlify build files in this folder publish only the storefront and proxy its API requests to a separate Python backend. To finish the connection:

1. Connect this GitHub repository to Netlify. Set NEXTBUY_API_ORIGIN in Netlify to the Render service URL after the backend is deployed.
2. Create a Render Blueprint from render.yaml. Enter your Netlify site URL for NEXTBUY_BASE_URL and choose a strong NEXTBUY_ADMIN_PASSWORD.
3. Copy the Render HTTPS service URL into Netlify's NEXTBUY_API_ORIGIN environment setting and redeploy the site.

The Render service uses a persistent disk for the SQLite database and uploads. Render requires a paid web service for persistent disks. Do not deploy the root folder as a public static directory; the Netlify build publishes only index.html, app.js, and features.js.