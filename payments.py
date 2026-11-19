import hashlib
import hmac
import json
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

JAZZCASH_SANDBOX_URL = "https://sandbox.jazzcash.com.pk/CustomerPortal/transactionmanagement/merchantform/"
EASYPAISA_SANDBOX_URL = "https://easypaystg.easypaisa.com.pk/easypay/Index.jsf"


def configured(provider: str) -> bool:
    if provider == "jazzcash":
        return all(os.environ.get(key, "").strip() for key in (
            "NEXTBUY_JAZZCASH_MERCHANT_ID", "NEXTBUY_JAZZCASH_PASSWORD", "NEXTBUY_JAZZCASH_INTEGRITY_SALT"))
    if provider == "easypaisa":
        return bool(os.environ.get("NEXTBUY_EASYPAISA_STORE_ID", "").strip() and
                    (os.environ.get("NEXTBUY_EASYPAISA_CALLBACK_SECRET", "").strip() or
                     os.environ.get("NEXTBUY_EASYPAISA_VERIFY_URL", "").strip()))
    return False


def jazzcash_secure_hash(fields: dict, salt: str) -> str:
    pairs = [(str(k), str(v)) for k, v in fields.items()
             if (str(k).lower().startswith("pp_") or str(k).lower().startswith("ppmpf_"))
             and k != "pp_SecureHash" and str(v) != ""]
    pairs.sort(key=lambda pair: pair[0])
    message = salt + "&" + "&".join(value for _, value in pairs)
    encoded = message.encode("utf-8").decode("latin-1").encode("latin-1")
    return hmac.new(salt.encode("utf-8"), encoded, hashlib.sha256).hexdigest().upper()


def create_request(provider: str, order: dict, base_url: str) -> dict:
    if not configured(provider):
        raise ValueError("Payment credentials are not configured for " + provider + ".")
    if provider == "jazzcash":
        now = datetime.now(timezone.utc)
        fields = {
            "pp_Version": "1.1", "pp_TxnType": os.environ.get("NEXTBUY_JAZZCASH_TXN_TYPE", "MWALLET"),
            "pp_Language": "EN", "pp_MerchantID": os.environ["NEXTBUY_JAZZCASH_MERCHANT_ID"],
            "pp_Password": os.environ["NEXTBUY_JAZZCASH_PASSWORD"], "pp_BankID": "TBANK",
            "pp_ProductID": "RETL", "pp_TxnRefNo": order["id"],
            "pp_Amount": str(int(order["total"]) * 100), "pp_TxnCurrency": "PKR",
            "pp_TxnDateTime": now.strftime("%Y%m%d%H%M%S"),
            "pp_TxnExpiryDateTime": (now + timedelta(minutes=15)).strftime("%Y%m%d%H%M%S"),
            "pp_BillReference": order["id"], "pp_Description": "NextBuy order " + order["id"],
            "pp_ReturnURL": base_url + "/api/payments/callback/jazzcash", "ppmpf_1": order["id"]
        }
        fields["pp_SecureHash"] = jazzcash_secure_hash(fields, os.environ["NEXTBUY_JAZZCASH_INTEGRITY_SALT"])
        action = os.environ.get("NEXTBUY_JAZZCASH_PAYMENT_URL", JAZZCASH_SANDBOX_URL).strip()
        if not action.startswith("https://"):
            raise ValueError("JazzCash payment URL must use HTTPS.")
        return {"action": action, "fields": fields}
    if provider == "easypaisa":
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y%m%d %H%M%S")
        action = os.environ.get("NEXTBUY_EASYPAISA_PAYMENT_URL", EASYPAISA_SANDBOX_URL).strip()
        if not action.startswith("https://"):
            raise ValueError("Easypaisa payment URL must use HTTPS.")
        return {"action": action, "fields": {
            "storeId": os.environ["NEXTBUY_EASYPAISA_STORE_ID"],
            "amount": "%.1f" % int(order["total"]),
            "postBackURL": base_url + "/api/payments/callback/easypaisa?" + urlencode({"order": order["id"]}),
            "orderRefNum": order["id"], "expiryDate": expiry, "autoRedirect": "1",
            "emailAddr": order.get("email", ""), "mobileNum": order.get("phone", "")
        }}
    raise ValueError("Unsupported payment provider.")


def _jazzcash_valid(data: dict, order: dict) -> tuple[bool, str]:
    salt = os.environ.get("NEXTBUY_JAZZCASH_INTEGRITY_SALT", "")
    supplied = str(data.get("pp_SecureHash", "")).upper()
    expected = jazzcash_secure_hash(data, salt) if salt else ""
    if not supplied or not hmac.compare_digest(supplied, expected):
        return False, "JazzCash signature check failed."
    if str(data.get("pp_MerchantID", "")) != os.environ.get("NEXTBUY_JAZZCASH_MERCHANT_ID", ""):
        return False, "JazzCash merchant check failed."
    if str(data.get("pp_TxnRefNo", "")) != order["id"]:
        return False, "JazzCash order reference did not match."
    if str(data.get("pp_Amount", "")) != str(int(order["total"]) * 100):
        return False, "JazzCash amount did not match."
    if str(data.get("pp_TxnCurrency", "PKR")) != "PKR":
        return False, "JazzCash currency did not match."
    if str(data.get("pp_ResponseCode", "")) != "000":
        return False, "JazzCash did not confirm payment."
    return True, str(data.get("pp_RetrievalReferenceNo", data.get("pp_TxnRefNo", "")))[:100]


def _easypaisa_valid(data: dict, order: dict) -> tuple[bool, str]:
    verify_url = os.environ.get("NEXTBUY_EASYPAISA_VERIFY_URL", "").strip()
    if verify_url:
        if not verify_url.startswith("https://"):
            return False, "Easypaisa verification URL must use HTTPS."
        request_data = json.dumps({"auth_token": str(data.get("auth_token", "")),
                                   "orderRefNum": order["id"],
                                   "storeId": os.environ.get("NEXTBUY_EASYPAISA_STORE_ID", ""),
                                   "callback": data}).encode("utf-8")
        request = Request(verify_url, data=request_data, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(256_000).decode("utf-8"))
        if (str(result.get("status", "")).lower() not in ("success", "paid", "0000") or
                str(result.get("orderRefNum", result.get("orderRefNumber", ""))) != order["id"] or
                not _amount_matches(result.get("amount"), order["total"])):
            return False, "Easypaisa verification did not confirm this order."
        return True, str(result.get("transactionId", order["id"]))[:100]

    secret = os.environ.get("NEXTBUY_EASYPAISA_CALLBACK_SECRET", "")
    supplied = str(data.get("signature", "")).lower()
    fields = {str(k): str(v) for k, v in data.items() if k != "signature"}
    message = "&".join(k + "=" + fields[k] for k in sorted(fields))
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest() if secret else ""
    if not supplied or not hmac.compare_digest(supplied, expected):
        return False, "Easypaisa signature check failed."
    ref = str(data.get("orderRefNum", data.get("orderRefNumber", "")))
    status = str(data.get("status", "")).lower()
    if ref != order["id"] or status not in ("success", "paid", "0000"):
        return False, "Easypaisa did not confirm this order."
    if not _amount_matches(data.get("amount"), order["total"]):
        return False, "Easypaisa amount did not match."
    return True, str(data.get("transactionId", ref))[:100]


def _amount_matches(value, expected) -> bool:
    if value is None or str(value).strip() == "":
        return False
    try:
        return Decimal(str(value)) == Decimal(str(int(expected)))
    except (InvalidOperation, ValueError, TypeError):
        return False


def verify_callback(provider: str, data: dict, order: dict) -> tuple[bool, str]:
    if provider == "jazzcash":
        return _jazzcash_valid(data, order)
    if provider == "easypaisa":
        return _easypaisa_valid(data, order)
    return False, "Unsupported payment provider."
