(function () {
  "use strict";

  var originalRenderAdmin = window.renderAdmin;

  async function api(path, method, body) {
    var options = { method: method || "GET", credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    var response = await fetch(path, options);
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || "The server could not complete that request.");
    return data;
  }

  function showError(id, message) {
    var node = document.getElementById(id);
    if (node) node.textContent = message || "";
  }

  function notifyError(error, id) {
    var message = error && error.message ? error.message : "Please try again.";
    if (error && (error.name === "TypeError" || message === "Failed to fetch")) {
      message = location.protocol === "file:"
        ? "This page was opened as a local file. Start the website server, then open http://localhost:8000 to sign in."
        : "The store server could not be reached. Reopen the store from its website address and try again.";
    }
    if (id) showError(id, message);
    else if (typeof toast === "function") toast(message);
  }

  function setProducts(list) {
    products = (Array.isArray(list) ? list : []).map(function (p) {
      return Object.assign({}, p, {
        id: Number(p.id), price: Number(p.price) || 0, oldPrice: Number(p.oldPrice) || 0,
        stock: Math.max(0, Number(p.stock) || 0), rating: Number(p.rating) || 0,
        colors: Array.isArray(p.colors) ? p.colors : [], sizes: Array.isArray(p.sizes) ? p.sizes : []
      });
    });
    save(K.p, products);
    if (typeof renderProducts === "function") renderProducts();
    if (typeof renderCart === "function") renderCart();
    if (typeof renderWish === "function") renderWish();
    if (typeof renderPos === "function") renderPos();
  }

  function setSession(nextUser) {
    user = nextUser || null;
    adminAuthenticated = !!(user && user.role === "admin");
    if (typeof renderHeader === "function") renderHeader();
    if (typeof showAccount === "function") showAccount();
  }

  async function refreshAdmin() {
    var data = await api("/api/admin/data");
    setProducts(data.products);
    orders = Array.isArray(data.orders) ? data.orders : [];
    users = Array.isArray(data.users) ? data.users : [];
    if (typeof originalRenderAdmin === "function") originalRenderAdmin();
    return data;
  }

  window.renderAdmin = function () {
    return refreshAdmin().catch(function (error) {
      notifyError(error);
      if (error.message.indexOf("sign-in") !== -1) {
        adminAuthenticated = false;
        if (typeof renderHeader === "function") renderHeader();
      }
    });
  };

  function renderOrders(list) {
    var target = document.getElementById("myOrders");
    if (!target) return;
    target.innerHTML = list.length ? list.map(function (order) {
      var items = Array.isArray(order.items) ? order.items : [];
      return '<article class="order-card"><div class="order-head"><span>' + esc(order.id) + '</span><span class="status ' + esc(String(order.status || "Pending").toLowerCase()) + '">' + esc(order.status || "Pending") + '</span></div><p>' + new Date(order.date).toLocaleDateString() + ' · ' + cash(order.total) + '</p><p><span>Payment method</span>: ' + esc(order.payment) + ' · <span>Payment status</span>: ' + esc(order.paymentStatus || "Unpaid") + '</p><p>' + items.map(function (item) { return esc(item.name) + ' × ' + item.qty; }).join("<br>") + '</p><p><span>Subtotal</span>: ' + cash(order.subtotal) + '<br><span>Discount</span>: -' + cash(order.discount) + '<br><span>Delivery</span>: ' + cash(order.shipping) + '</p><p>Delivering to ' + esc(order.city) + '</p></article>';
    }).join("") : '<div class="empty">No orders yet.</div>';
    modal("ordersModal", true);
  }

  async function loadMyOrders() {
    if (!user || user.guest || !user.role) {
      renderOrders([]);
      return;
    }
    try {
      var data = await api("/api/orders");
      renderOrders(Array.isArray(data.orders) ? data.orders : []);
    } catch (error) {
      renderOrders([]);
      notifyError(error);
    }
  }

  function splitList(id) {
    var node = document.getElementById(id);
    return node ? node.value.split(",").map(function (x) { return x.trim(); }).filter(Boolean) : [];
  }

  function productPayload(form) {
    var id = Number(document.getElementById("editProductId").value);
    var previous = id ? getProduct(id) : null;
    return {
      name: document.getElementById("adminName").value.trim(),
      category: document.getElementById("adminCategory").value,
      price: Number(document.getElementById("adminPrice").value),
      oldPrice: Number(document.getElementById("adminOldPrice").value),
      stock: Number(document.getElementById("adminStock").value),
      rating: Number(document.getElementById("adminRating").value || 4.5),
      image: document.getElementById("adminImage").value.trim() || (previous ? previous.image : ""),
      description: document.getElementById("adminDescription").value.trim(),
      colors: splitList("adminColors"), sizes: splitList("adminSizes"),
      gallery: splitList("adminGalleryUrls"), reviews: previous ? previous.reviews : 0
    };
  }

  async function uploadProductImage(file) {
    var formData = new FormData();
    formData.append("image", file);
    var response = await fetch("/api/admin/product-image", {
      method: "POST", credentials: "same-origin", body: formData
    });
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || "The image could not be uploaded.");
    return data.image;
  }

  async function submitProduct(form) {
    showError("productError", "");
    var id = Number(document.getElementById("editProductId").value);
    try {
      var payload = productPayload(form);
      var imageFile = document.getElementById("adminImageFile").files[0];
      if (imageFile) {
        if (imageFile.size > 2 * 1024 * 1024) throw new Error("Image must be 2 MB or smaller.");
        payload.image = await uploadProductImage(imageFile);
      }
      var galleryFiles = Array.prototype.slice.call(document.getElementById("adminGalleryFiles").files || []);
      for (var i = 0; i < galleryFiles.length; i++) {
        if (galleryFiles[i].size > 2 * 1024 * 1024) throw new Error("Each gallery image must be 2 MB or smaller.");
        payload.gallery.push(await uploadProductImage(galleryFiles[i]));
      }
      payload.gallery = Array.from(new Set(payload.gallery)).slice(0, 10);
      await api(id ? "/api/admin/products/" + id : "/api/admin/products", id ? "PUT" : "POST", payload);
      modal("productModal", false);
      await refreshAdmin();
      if (typeof toast === "function") toast(id ? "Product updated." : "Product added.");
    } catch (error) { notifyError(error, "productError"); }
  }

  async function submitOrder(form) {
    showError("checkoutError", "");
    var data = {
      customer: document.getElementById("customerName").value.trim(),
      phone: document.getElementById("customerPhone").value.trim(),
      email: document.getElementById("customerEmail").value.trim(),
      city: document.getElementById("customerCity").value.trim(),
      address: document.getElementById("customerAddress").value.trim(),
      payment: document.getElementById("paymentMethod").value,
      couponCode: window.nextbuyAppliedCoupon || "",
      items: cart.map(function (item) { return { id: item.id, qty: item.qty, size: item.size || "", color: item.color || "" }; })
    };
    if (data.phone.replace(/\D/g, "").length < 10) { showError("checkoutError", "Enter a valid phone number."); return; }
    if (!data.items.length) { showError("checkoutError", "Your cart is empty."); return; }
    try {
      var result = await api("/api/orders", "POST", data);
      lastOrder = result.order;
      setProducts(result.products);
      cart = [];
      save(K.c, cart);
      renderCart();
      renderProducts();
      window.nextbuyAppliedCoupon = "";
      if (result.paymentRequest && typeof window.nextbuyRedirectPayment === "function") {
        form.reset();
        modal("checkoutModal", false);
        window.nextbuyRedirectPayment(result.paymentRequest);
        return;
      }
      document.getElementById("successMessage").textContent = "Order " + lastOrder.id + " has been placed. We will contact you to confirm delivery.";
      form.reset();
      modal("checkoutModal", false);
      modal("successModal", true);
      if (user && user.role === "customer") {
        var orderData = await api("/api/orders");
        orders = orderData.orders || [];
      }
      if (typeof toast === "function") toast("Order placed successfully.");
    } catch (error) { notifyError(error, "checkoutError"); }
  }

  async function submitPosSale() {
    showError("posError", "");
    if (!posCart.length) { showError("posError", "Add at least one product before completing the sale."); return; }
    var payload = {
      customer: document.getElementById("posCustomer").value.trim() || "Walk-in Customer",
      phone: document.getElementById("posPhone").value.trim(),
      payment: document.getElementById("posPayment").value,
      items: posCart.map(function (item) { return { id: item.id, qty: item.qty }; })
    };
    try {
      var data = await api("/api/admin/pos", "POST", payload);
      posCart = [];
      document.getElementById("posCustomer").value = "";
      document.getElementById("posPhone").value = "";
      setProducts(data.products);
      await refreshAdmin();
      var order = data.order;
      var receipt = document.getElementById("posReceipt");
      receipt.innerHTML = '<h3>Sale completed</h3><p><strong>' + esc(order.id) + '</strong> - ' + new Date(order.date).toLocaleString() + '</p><p>Customer: ' + esc(order.customer) + ' | Payment: ' + esc(order.payment) + '</p><ul>' + order.items.map(function (item) { return '<li>' + esc(item.name) + ' x ' + item.qty + ' - ' + cash(item.subtotal) + '</li>'; }).join("") + '</ul><p><strong>Total: ' + cash(order.total) + '</strong></p><button type="button" data-print-pos>Print Receipt</button>';
      receipt.hidden = false;
      if (typeof toast === "function") toast("POS sale " + order.id + " completed.");
    } catch (error) { notifyError(error, "posError"); }
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var actions = {
      emailPanel: function () {
        showError("loginError", "");
        return api("/api/auth/login", "POST", { email: document.getElementById("loginEmail").value.trim(), password: document.getElementById("loginPassword").value }).then(function (data) {
          setSession(data.user); modal("accountModal", false); if (typeof toast === "function") toast("Welcome back, " + data.user.name + ".");
        }).catch(function (error) { notifyError(error, "loginError"); });
      },
      phonePanel: function () {
        showError("phoneError", "");
        return api("/api/auth/phone/verify", "POST", { phone: document.getElementById("loginPhone").value.trim(), code: document.getElementById("otpCode").value.trim() }).then(function (data) {
          setSession(data.user); modal("accountModal", false); if (typeof toast === "function") toast("Phone login successful.");
        }).catch(function (error) { notifyError(error, "phoneError"); });
      },
      signupForm: function () {
        showError("signupError", "");
        return api("/api/auth/signup", "POST", {
          name: document.getElementById("signupName").value.trim(), email: document.getElementById("signupEmail").value.trim(),
          phone: document.getElementById("signupPhone").value.trim(), password: document.getElementById("signupPassword").value
        }).then(function (data) { setSession(data.user); modal("accountModal", false); if (typeof toast === "function") toast("Account created."); })
          .catch(function (error) { notifyError(error, "signupError"); });
      },
      adminLoginForm: function () {
        showError("adminError", "");
        var submit = document.getElementById("adminLoginSubmit");
        submit.disabled = true; submit.textContent = "Signing in...";
        return api("/api/admin/login", "POST", { email: document.getElementById("adminEmail").value.trim(), password: document.getElementById("adminPassword").value }).then(async function (data) {
          setSession(data.user); modal("adminLoginModal", false); history.replaceState(null, "", location.pathname + location.search + "#admin");
          if (typeof showAdmin === "function") showAdmin();
          await refreshAdmin();
        }).catch(function (error) { notifyError(error, "adminError"); })
          .finally(function () { submit.disabled = false; submit.textContent = "Sign in to dashboard"; document.getElementById("adminPassword").value = ""; });
      },
      checkoutForm: function () { return submitOrder(form); },
      adminProductForm: function () { return submitProduct(form); }
    };
    if (!actions[form.id]) return;
    event.preventDefault(); event.stopImmediatePropagation();
    actions[form.id]();
  }, true);

  document.addEventListener("click", function (event) {
    var target = event.target;
    var requestOtp = target.closest && target.closest("#requestOtp");
    var ordersButton = target.closest && target.closest("#ordersButton, #accountOrders");
    var logoutButton = target.closest && target.closest("#logoutButton, #adminLogout");
    var posButton = target.closest && target.closest("#completePosSale");
    var editButton = target.closest && target.closest("[data-edit]");
    var deleteButton = target.closest && target.closest("[data-delete]");
    if (requestOtp) {
      event.preventDefault(); event.stopImmediatePropagation(); showError("phoneError", "");
      api("/api/auth/phone/start", "POST", { phone: document.getElementById("loginPhone").value.trim() }).then(function () {
        document.getElementById("otpFields").hidden = false; document.getElementById("otpCode").focus();
      }).catch(function (error) { notifyError(error, "phoneError"); });
      return;
    }
    if (ordersButton) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (ordersButton.id === "accountOrders") modal("accountModal", false);
      loadMyOrders(); return;
    }
    if (logoutButton) {
      event.preventDefault(); event.stopImmediatePropagation();
      api("/api/logout", "POST", {}).catch(function () {}).finally(function () {
        setSession(null); history.replaceState(null, "", location.pathname + location.search); if (typeof hideAdmin === "function") hideAdmin();
        if (typeof toast === "function") toast("Logged out.");
      });
      return;
    }
    if (posButton) { event.preventDefault(); event.stopImmediatePropagation(); submitPosSale(); return; }
    if (editButton) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (typeof productForm === "function") productForm(editButton.dataset.edit);
      return;
    }
    if (deleteButton) {
      event.preventDefault(); event.stopImmediatePropagation();
      var id = Number(deleteButton.dataset.delete), product = getProduct(id);
      if (!product || !confirm("Delete " + product.name + " from the catalog?")) return;
      api("/api/admin/products/" + id, "DELETE").then(refreshAdmin).catch(function (error) { notifyError(error); });
    }
  }, true);

  document.addEventListener("change", function (event) {
    var select = event.target.closest && event.target.closest("select[data-status]");
    if (!select) return;
    event.preventDefault(); event.stopImmediatePropagation();
    api("/api/admin/orders/" + encodeURIComponent(select.dataset.status), "PATCH", { status: select.value })
      .then(refreshAdmin).then(function () { if (typeof toast === "function") toast("Order status updated."); })
      .catch(function (error) { notifyError(error); });
  }, true);

  // Admin login page: show/hide password, back button, and clean the URL when the page is dismissed.
  (function adminLoginUi() {
    var modalNode = document.getElementById("adminLoginModal");
    var toggle = document.getElementById("adminPasswordToggle");
    var back = document.getElementById("adminLoginBack");
    if (!modalNode) return;
    function leaveAdminUrl() {
      if (!adminAuthenticated && location.hash.toLowerCase() === "#admin") history.replaceState(null, "", location.pathname + location.search);
    }
    toggle.addEventListener("click", function () {
      var input = document.getElementById("adminPassword"), show = input.type === "password";
      input.type = show ? "text" : "password";
      toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
      toggle.firstElementChild.className = show ? "fa-regular fa-eye-slash" : "fa-regular fa-eye";
    });
    back.addEventListener("click", function () { modal("adminLoginModal", false); });
    new MutationObserver(function () { if (!modalNode.classList.contains("show")) leaveAdminUrl(); })
      .observe(modalNode, { attributes: true, attributeFilter: ["class"] });
  }());

  document.addEventListener("DOMContentLoaded", async function () {
    orders = []; users = []; user = null; adminAuthenticated = false;
    renderHeader(); showAccount();
    try {
      var boot = await api("/api/bootstrap", "POST", { products: seed });
      setProducts(boot.products);
      if (typeof window.nextbuyRouteHash === "function") window.nextbuyRouteHash();
      var identity = await api("/api/me");
      setSession(identity.user);
      if (adminAuthenticated) {
        if (location.hash.toLowerCase() === "#admin") { modal("adminLoginModal", false); showAdmin(); }
        else renderHeader();
      }
    } catch (error) {
      var warning = document.getElementById("backendNotice");
      if (warning) { warning.hidden = false; warning.textContent = "Store server is not connected. Start server.py and open http://localhost:8000. Friends need the hosted website address to sign up from their own devices."; }
      else if (typeof toast === "function") toast("Start the NextBuy server to load the store.");
      console.error("NextBuy backend connection failed:", error);
    }
  });

  // ----- Extras: order tracking, WhatsApp visibility, scroll spy, back-to-top -----
  function renderTrack(order) {
    var items = (order.items || []).map(function (i) { return esc(i.name) + " × " + i.qty; }).join("<br>");
    var steps = ["Pending", "Shipped", "Delivered"], at = steps.indexOf(order.status);
    var bar = order.status === "Cancelled" ? '<p class="status cancelled">Cancelled</p>' :
      '<div class="track-steps">' + steps.map(function (st, n) { return '<span class="' + (n <= at ? "done" : "") + '">' + st + "</span>"; }).join("") + "</div>";
    document.getElementById("trackResult").innerHTML = '<article class="order-card"><div class="order-head"><span>' + esc(order.id) + '</span><span class="status ' + esc(String(order.status).toLowerCase()) + '">' + esc(order.status) + "</span></div>" + bar + "<p>" + new Date(order.date).toLocaleDateString() + " · " + cash(order.total) + "</p><p><span>Payment method</span>: " + esc(order.payment) + "</p><p>" + items + "</p><p>Delivering to " + esc(order.city) + "</p></article>";
  }

  document.addEventListener("click", function (event) {
    var t = event.target.closest && event.target.closest("[data-track]");
    if (!t) return;
    event.preventDefault();
    document.getElementById("trackError").textContent = "";
    document.getElementById("trackResult").innerHTML = "";
    modal("trackModal", true);
  });

  document.addEventListener("submit", function (event) {
    if (event.target.id !== "trackForm") return;
    event.preventDefault();
    showError("trackError", "");
    document.getElementById("trackResult").innerHTML = "";
    var code = document.getElementById("trackCode").value.trim(), phone = document.getElementById("trackPhone").value.trim();
    api("/api/orders/track", "POST", { code: code, phone: phone }).then(function (data) { renderTrack(data.order); })
      .catch(function (error) { notifyError(error, "trackError"); });
  });

  document.addEventListener("DOMContentLoaded", function () {
    var wa = document.getElementById("whatsappButton");
    if (wa && !/^92\d{10}$/.test(typeof WHATSAPP_NUMBER === "string" ? WHATSAPP_NUMBER : "")) wa.hidden = true;

    var toTop = document.getElementById("toTop");
    var links = [].slice.call(document.querySelectorAll("#categoryNav a"));
    function onScroll() {
      if (toTop) toTop.hidden = window.scrollY < 600;
      var y = window.scrollY + 160, current = "#home";
      ["#home", "#categories", "#products", "#footer"].forEach(function (id) {
        var el = document.querySelector(id);
        if (el && el.offsetTop <= y) current = id;
      });
      links.forEach(function (a) {
        var on = a.getAttribute("href") === current && !a.hasAttribute("data-track") && a.dataset.nav !== "deals" && !(current === "#products" && a.textContent !== "Shop") ;
        if (a.getAttribute("href") === "#products" && a.dataset.nav === "deals") on = false;
        a.classList.toggle("active", on);
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    if (toTop) toTop.addEventListener("click", function () { window.scrollTo({ top: 0, behavior: "smooth" }); });
  });

  // ----- Hide phone login when no SMS provider is configured -----
  document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/config", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (cfg) {
      if (cfg && cfg.phoneLogin === false) {
        var tab = document.querySelector('[data-auth="phonePanel"]');
        if (tab) tab.style.display = "none";
        var panel = document.getElementById("phonePanel");
        if (panel) panel.style.display = "none";
      }
    }).catch(function () {});
  });

  // ----- Policy dialogs (Privacy, Terms, Returns, About) -----
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("[data-policy]");
    if (link) {
      event.preventDefault();
      var dlg = document.getElementById("policy-" + link.dataset.policy);
      if (dlg && typeof dlg.showModal === "function") dlg.showModal();
      return;
    }
    var close = event.target.closest && event.target.closest("[data-policy-close]");
    if (close) { var d = close.closest("dialog"); if (d) d.close(); }
    else if (event.target.matches && event.target.matches("dialog.policy-dialog")) event.target.close();
  });
}());
