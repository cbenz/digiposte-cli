# Digiposte API documentation

Digiposte exposes **two distinct APIs**, which are often confused. This document
clarifies their roles, URLs and authentication.

## Summary table

| | **Partner “OKAPI” API** | **Personal vault internal API** |
| --- | --- | --- |
| URL | `https://api.laposte.fr/digiposte/v3` | `https://api.digiposte.fr/api/v3` |
| Role | **Send** certified documents into Digiposte vaults; access documents shared with the partner | **Read / list / download your own vault** (used by the web SPA) |
| Auth | OKAPI key → `X-Okapi-Key` header | User session token → `Authorization: Bearer` |
| Endpoints | Document deposit, procedures, partner management (`partnerOauthToken` + `senderPid`) | `/dashboard`, `/folders`, `/documents/search`, `/document/{id}/content`… |
| Access to your own vault? | ❌ No | ✅ Yes |
| Documentation | <https://developer.laposte.fr/catalog-apis/digiposte@3> | (private — deduced from the SPA traffic) |

---

## 1. Partner “OKAPI” API (`api.laposte.fr/digiposte/v3`)

Official catalogue: <https://developer.laposte.fr/catalog-apis/digiposte@3>

> *“Send certified documents into Digiposte and access the documents with
> probative value of Digiposte users.”*

- Authentication: subscription key sent in the `X-Okapi-Key: <token>` header.
- Usage: a partner deposits documents (invoices, pay slips…) into a user's
  vault, or reads documents that this user has shared with them.
- **Does not allow** listing/downloading the personal vault of the account
  holder (the `/documents`, `/folders`, `/dashboard` routes answer 404 there).

## 2. Personal vault internal API (`api.digiposte.fr/api/v3`)

This is the API used by the SPA at <https://secure.digiposte.fr> (personal
safe). The swagger is served at
<https://api.digiposte.fr/api/v3/swagger.json> (local copy:
[docs/swagger.json](./swagger.json)) — it is **this one** that contains the
`/documents` routes (“Returns all user's documents”), `/folders`, etc.

### Authentication (OAuth2 flow / session token)

```
SPA (secure.digiposte.fr)
   │  La Poste / Keycloak SSO login (moncompte.laposte.fr)
   │  → session cookies + XSRF-TOKEN cookie on secure.digiposte.fr
   ▼
GET https://secure.digiposte.fr/rest/security/token
   (headers: session cookies + X-XSRF-TOKEN = XSRF-TOKEN cookie value)
   → {"access_token": "...", "expires_at": <unix>, "is_token_consolidated": …}
   ▼
api.digiposte.fr/api/v3/...   (Authorization: Bearer <access_token>)
```

Useful endpoints for a vault backup:

| Endpoint | Method | Usage |
| --- | --- | --- |
| `/documents/search?max_results=1000&sort=CREATION_DATE` | POST | list documents, body `{"locations":["SAFE","INBOX"],"folder_id":""}` |
| `/folders` | GET | folder tree |
| `/document/{id}/content` | GET | raw (byte) content of a document |
| `/dashboard` | GET | vault information |

Expected headers: `Authorization: Bearer`, `X-API-VERSION-MINOR: 2`,
`Accept: application/json`, `Content-Type: application/json`.

> ⚠️ This token is the one of the user **logged in** in the browser. It cannot
> be replaced by an OKAPI key (API 1). That is why the tool opens a browser
> (SSO login + possible CAPTCHA/2FA), then caches the token for later runs.
