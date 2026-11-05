import os, time, base64, httpx
from jose import jwt

KEYS = ["GITHUB_APP_ID", "GITHUB_APP_SLUG", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
        "GITHUB_APP_PRIVATE_KEY_B64", "FRONTEND_URL"]
for k in KEYS:
    v = os.environ.get(k)
    print(f"{k}: " + ("MISSING" if v is None else f"len={len(v)} edge_whitespace={v != v.strip()}"))
print("FRONTEND_URL =", os.environ.get("FRONTEND_URL"))

# App ID + private key
slug = None
try:
    pem = base64.b64decode(os.environ["GITHUB_APP_PRIVATE_KEY_B64"]).decode()
    now = int(time.time())
    tok = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(os.environ["GITHUB_APP_ID"])},
                     pem, algorithm="RS256")
    r = httpx.get("https://api.github.com/app",
                  headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
    slug = r.json().get("slug") if r.status_code == 200 else None
    print("app check:", r.status_code, slug or r.json().get("message"))
except Exception as e:
    print("app check failed:", type(e).__name__, e)
print("GITHUB_APP_SLUG matches the app:", os.environ.get("GITHUB_APP_SLUG") == slug)

# Client ID + secret (sends a deliberately bad code)
try:
    r = httpx.post("https://github.com/login/oauth/access_token",
                   headers={"Accept": "application/json"},
                   data={"client_id": os.environ["GITHUB_CLIENT_ID"],
                         "client_secret": os.environ["GITHUB_CLIENT_SECRET"], "code": "x"})
    print("client check:", r.json().get("error"))
except Exception as e:
    print("client check failed:", type(e).__name__, e)