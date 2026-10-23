# Embedding Labs: Integration Guide

How to show a labs report inside your own system's pages, as the person viewing it, with no second sign-in. This page is for **engineers building the embedding system**. For what viewers see and how a system is registered, see [Embedding Labs in Other Systems](../embedding-labs.md).

!!! info "Standards, not a labs-specific protocol"
    Everything here is standard: a JWT used as an OAuth authorization grant ([RFC 7523](https://www.rfc-editor.org/rfc/rfc7523)), public keys published as a JWKS ([RFC 7517](https://www.rfc-editor.org/rfc/rfc7517)), OAuth error responses ([RFC 6749 §5.2](https://www.rfc-editor.org/rfc/rfc6749#section-5.2)), an iframe, and `postMessage`. Any language with a JWT library can do it.

## See It Working First

A **live reference embedder** runs inside labs at [/embed-example/](https://labs.connect.dimagi.com/embed-example/) (labs sign-in needed). It plays the part of another system: it holds its own key, publishes its own public keys, signs a statement for you, exchanges it at the token endpoint, and frames a labs report. It does both token handoffs (in the address, and by `postMessage`) and shows every message the frame sends. A "reload the frame" button shows a frame asking for a fresh token.

Its code is written the way an outside system would write it. It imports nothing from labs' own embedding code, and is short enough to read top to bottom:

| File | What it shows |
| --- | --- |
| [`embed_example/embedder.py`](https://github.com/dimagi-internal/connect-labs/blob/main/connect_labs/embed_example/embedder.py) | The whole server side: the key, the JWKS, signing the statement, the token exchange. PyJWT, cryptography and an HTTP client only. |
| [`embed_example/views.py`](https://github.com/dimagi-internal/connect-labs/blob/main/connect_labs/embed_example/views.py) | The example system's three endpoints: its page, a token endpoint for the page's script, and its published keys. |
| [`templates/embed_example/page.html`](https://github.com/dimagi-internal/connect-labs/blob/main/connect_labs/templates/embed_example/page.html) | The browser side: the iframe, both handoffs, and listening to the frame. |
| [`embed_example/tests/`](https://github.com/dimagi-internal/connect-labs/tree/main/connect_labs/embed_example/tests) | A round trip through labs' real token endpoint with the example's own key. |

!!! note "What the example cannot show"
    The example is served from labs' own address, so the frame is not cross-origin. Your system's page will be. Everything else runs exactly as it will for you, but the cross-origin parts (the `frame-ancestors` check against your origin, and the browser keeping the frame's cookie under your site) are only exercised once you embed from your own origin.

!!! note "Why it is safe to leave running"
    The example only ever names the person already signed in to labs. Its code never takes a username from a request. Labs also limits its registration to **generated data** (the client's "generated data only" setting), checked at token time over everything the report reads. So even a leaked example key could show nothing real.

## At a Glance

| Item | Value |
| --- | --- |
| Labs | `https://labs.connect.dimagi.com` |
| Token endpoint | `POST /embed/token/` |
| Frame address | `/embed/c/<client id>/<kind>/<id>/` |
| Signing algorithms | `EdDSA` (Ed25519, recommended), `ES256` (P-256), `RS256` (2048-bit or larger). Never HMAC. |
| Statement lifetime | At most 300 seconds; single use. |
| Token lifetime | 120 seconds; single use. |
| Session lifetime | Set per system at registration (default one hour). |
| What you need from labs | A registered **client id**, with your public keys, your allowed origins, and the reports you may show. |

## Before You Start

Ask in **#engineering-connect** to be registered. Send:

- your **public** keys (a JWKS) or the `https://` address where you publish them;
- the **origins** that will frame labs, exactly (`https://connect.dimagi.com`);
- the **reports** to show (their labs addresses).

You get back a **client id**. Only a system that decides who people are (today, Connect) can currently show reports as a named person. See [What's next](../embedding-labs.md#whats-next) for other systems.

---

## Steps

### 1. Make a key pair and register it

Use an Ed25519 key (recommended), an ES256 (P-256) key, or an RSA key of at least 2048 bits signing RS256. Keep the private key on your server. Send labs the public key as a JWKS, or publish it at an `https://` address.

```python
# pip install "pyjwt[crypto]"
import json
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from jwt.algorithms import OKPAlgorithm

key = ed25519.Ed25519PrivateKey.generate()
pem = key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
public_jwk = OKPAlgorithm.to_jwk(key.public_key(), as_dict=True)
public_jwk["kid"] = "connect-2026-10"  # any name; put it in each JWT header
print(json.dumps({"keys": [public_jwk]}))  # send this (public) to labs
```

Give labs your JWKS (or its URL), your **client id**, the **origins** that will frame labs, and the **reports** to show. To rotate a key, publish the new key alongside the old, switch to signing with it, then remove the old one. A published JWKS is re-read within an hour, or sooner when a `kid` labs has not seen arrives.

### 2. Sign a statement for each page load

The statement is a JWT. Make a new one every time you render a page with a labs frame.

| Claim | Value |
| --- | --- |
| `iss` | Your client id. |
| `sub` | The person's **Connect username** (the account labs knows them by). |
| `aud` | `https://labs.connect.dimagi.com/embed/token/` |
| `iat` | Now, in seconds since the epoch. |
| `exp` | At most 300 seconds after `iat`. 60 is plenty. |
| `jti` | A new random id. Each statement works once. |
| `resource` | The frame address: `https://labs.connect.dimagi.com/embed/c/<client id>/<kind>/<id>/` |

Put your key's `kid` in the JWT header when you have more than one key.

### 3. Exchange it for a token, server to server

```
POST https://labs.connect.dimagi.com/embed/token/
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=<the JWT>
```

A success:

```json
{
  "access_token": "<one-time token>",
  "token_type": "Bearer",
  "expires_in": 120,
  "embed_url": "https://labs.connect.dimagi.com/embed/c/connect/workflow/123/"
}
```

The whole step in Python:

```python
import time, uuid, jwt, requests

LABS = "https://labs.connect.dimagi.com"

def labs_embed(username: str, kind: str, resource_id: int) -> dict:
    now = int(time.time())
    resource = f"{LABS}/embed/c/connect/{kind}/{resource_id}/"
    assertion = jwt.encode(
        {"iss": "connect", "sub": username, "aud": f"{LABS}/embed/token/",
         "iat": now, "exp": now + 60, "jti": str(uuid.uuid4()), "resource": resource},
        PRIVATE_KEY, algorithm="EdDSA", headers={"kid": "connect-2026-10"},
    )
    response = requests.post(f"{LABS}/embed/token/", timeout=10, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    })
    response.raise_for_status()
    return response.json()  # embed_url, access_token
```

Never send the token request from the browser: the private key must stay on your server.

### 4. Put the frame on the page

Hand the token to the frame in the address's **fragment** (after `#`). Browsers never send the fragment to a server, so it stays out of logs.

```html
<iframe src="{{ embed_url }}#token={{ access_token }}"
        title="Stock review"
        style="width: 100%; min-height: 800px; border: 0"></iframe>
```

Or load the frame without a token and post it in once the frame asks:

```javascript
const LABS = "https://labs.connect.dimagi.com";
const frame = document.getElementById("labs-frame");

window.addEventListener("message", async (event) => {
  if (event.origin !== LABS || event.source !== frame.contentWindow) return;
  const msg = event.data || {};
  if (msg.type !== "labs-embed") return;
  if (msg.event === "needs-token") {
    const { access_token } = await fetch("/my-server/labs-token?report=123").then((r) => r.json());
    frame.contentWindow.postMessage({ type: "labs-embed-token", token: access_token }, LABS);
  }
});
```

Always name labs' origin when posting, and check `event.origin` when listening.

**Your page's own security policy** must allow the frame: include `https://labs.connect.dimagi.com` in your `frame-src` (or `child-src`) if you set a Content-Security-Policy.

### 5. Messages the frame sends

The frame posts these to its parent, addressed only to your registered origins:

| Message | When | What to do |
| --- | --- | --- |
| `{type: "labs-embed", event: "needs-token", kind, id}` | The frame loaded with no usable session: without a token, or reloaded. | Mint a new token on your server and post it in as `{type: "labs-embed-token", token}`. |
| `{type: "labs-embed", event: "ready", kind, id}` | The report is drawn. | Nothing; useful for hiding a spinner. |
| `{type: "labs-embed", event: "error", error, kind, id}` | The token could not start a session (used, expired, for another report). | Mint a new token, or show your own message. |

A token posted in at any time **replaces** the frame's session, so when the person signed in to your system changes, post in a token for the new person.

### 6. Errors from the token endpoint

Errors follow OAuth (RFC 6749): a JSON body `{"error": ..., "error_description": ...}`. The description is safe to show to a person.

| `error` | Usually means |
| --- | --- |
| `invalid_request` | The `assertion` parameter is missing. |
| `unsupported_grant_type` | `grant_type` is not `urn:ietf:params:oauth:grant-type:jwt-bearer`. |
| `invalid_client` (401) | Unknown or inactive client id, or the signature does not check out: wrong key, unknown `kid`, or an algorithm labs does not accept (HMAC and `none` never are). |
| `invalid_grant` | Wrong `aud`, expired, valid for more than five minutes, `jti` missing or already used, no labs account for `sub`, the person has never signed in to labs, or **the person cannot open this report**. |
| `unauthorized_client` | This client is not allowed to say who people are (not an identity authority). |
| `invalid_target` | `resource` is not a labs embed address, or names a report this client is not registered for. |


---

## Troubleshooting

| Symptom | Check |
| --- | --- |
| The frame is blank and the browser console says framing "violates the following Content Security Policy directive: frame-ancestors" | The page's origin is not in the system's **Allowed origins**. Origins must match exactly, including `https://` and any port. |
| The frame says *"This view is shown inside another system."* | The frame address was opened on its own, not inside a frame. Tokens are only taken inside a frame. |
| The frame shows *"This embed token has already been used"* or *"has expired"* | A token is single-use and lasts two minutes. Mint one per page load, just before rendering the page, and do not cache it. |
| `invalid_grant`: *"That person has not signed in to labs yet."* | Ask them to sign in to labs once at [labs.connect.dimagi.com](https://labs.connect.dimagi.com). |
| `invalid_grant`: *"This person cannot open that workflow."* | They do not have access to the report's opportunity or programme. Check their Connect access, or check that the resource entry names the right scope. |
| The report draws but says an opportunity "is not accessible to your account" | The person can open the report but not everything it reads. That is their real access, applied as it would be in labs. |
| Reloading only the frame shows a loading message | Expected: each load needs a new token. Answer the frame's `needs-token` message (step 5). |
| A button in the embedded report reports *"This embedded view is read-only."* | Embedded reports are read-only: saving, completing a run and actions are refused. Open the report in labs to change it. |
| A report left open for a long time stops loading new data | Its session lasts the system's **Session seconds** (an hour by default), and the person's Connect sign-in must still be valid. Post in a new token, or reload the page. |


---

## Reference

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/embed/token/` | `POST` (form) | Exchange a signed statement for a one-time token. Server to server. |
| `/embed/c/<client id>/<kind>/<id>/` | `GET` | The frame. Framable only by your registered origins. |
| `/embed/session/` | `POST` (JSON) | Used by the frame itself to trade the token for its session. You never call it. |

**Kinds** available to embed: `workflow` (a labs workflow or page; `id` is its number). More kinds will be added; the flow above does not change when they are.

**Compatibility.** Labs treats the claims, endpoints, messages and errors on this page as a public interface. A change that would break an embedding system is announced here, and in **#engineering-connect**, before it ships.
