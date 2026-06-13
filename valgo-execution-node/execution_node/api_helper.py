"""Minimal ShoonyaApiPy — replicates api_helper.py from Shoonya's SDK samples."""
import hashlib
import json
import requests


LOGIN_HOST = "https://trade.shoonya.com/NorenWClientAPI/"
API_HOST   = "https://api.shoonya.com/NorenWClientAPI/"


class ShoonyaApiPy:
    def __init__(self):
        self._session = requests.Session()
        self._token   = None
        self._uid     = None

    def _post(self, endpoint: str, payload: dict, jkey: str | None = None,
              use_login_host: bool = False) -> dict:
        body = "jData=" + json.dumps(payload, separators=(",", ":"))
        if jkey:
            body += "&jKey=" + jkey
        host = LOGIN_HOST if use_login_host else API_HOST
        r = self._session.post(
            host + endpoint,
            data=body,
            headers={"Content-Type": "text/plain"},
            timeout=15,
        )
        if not r.ok:
            print(f"HTTP {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
        return r.json()

    def login(self, userid: str, password: str, twoFA: str,
              vendor_code: str, api_secret: str, imei: str = "abc1234") -> dict:
        appkey = hashlib.sha256(f"{userid}{api_secret}".encode()).hexdigest()
        payload = {
            "uid":        userid,
            "pwd":        password,
            "factor2":    twoFA,
            "vc":         vendor_code,
            "appkey":     appkey,
            "imei":       imei,
            "source":     "API",
            "apkversion": "W2_20250926",
        }
        ret = self._post("QuickAuth", payload, use_login_host=True)
        if ret and ret.get("stat") == "Ok":
            self._token = ret["susertoken"]
            self._uid   = userid
        return ret

    def get_limits(self):
        return self._post("RmsLimitsLite", {"uid": self._uid, "actid": self._uid}, self._token)

    def get_quotes(self, exchange: str, token: str):
        return self._post("GetQuotes", {"uid": self._uid, "exch": exchange, "token": token}, self._token)
