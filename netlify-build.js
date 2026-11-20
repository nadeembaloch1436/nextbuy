"use strict";

const fs = require("node:fs");
const path = require("node:path");

const root = __dirname;
const output = path.join(root, "public");
const configuredOrigin = (process.env.NEXTBUY_API_ORIGIN || "").trim();

let backend;
try {
  backend = new URL(configuredOrigin);
} catch {
  throw new Error("Set NEXTBUY_API_ORIGIN in Netlify to the HTTPS URL of the deployed Python API.");
}

if (
  backend.protocol !== "https:" ||
  backend.username ||
  backend.password ||
  backend.pathname !== "/" ||
  backend.search ||
  backend.hash
) {
  throw new Error("NEXTBUY_API_ORIGIN must be a plain HTTPS origin, such as https://your-api.onrender.com.");
}

fs.mkdirSync(output, { recursive: true });
for (const file of ["index.html", "app.js", "features.js"]) {
  fs.copyFileSync(path.join(root, file), path.join(output, file));
}

const origin = backend.origin;
const redirects = [
  "/api/* " + origin + "/api/:splat 200",
  "/uploads/* " + origin + "/uploads/:splat 200",
  "/* /index.html 200",
  ""
].join("\n");

fs.writeFileSync(path.join(output, "_redirects"), redirects, "utf8");