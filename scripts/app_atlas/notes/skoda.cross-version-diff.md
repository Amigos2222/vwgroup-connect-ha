**8.15.0 → 8.16.0 (versionCode 260821007)** — strictly additive. Nothing was
removed or renamed, and auth, IDP, headers, Firebase config and the MQTT push
setup are all byte-identical, so no existing call path changes behaviour.

| Area | Delta |
|---|---|
| `api/vN` routes | 5 added, 0 removed |
| Predictive-maintenance body | `+predictions[]` |
| Capability ids | `+PUBLIC_API_KEY_MANAGEMENT`, `+BATTERY_HEALTH_STATE` |
| New module | Laura agent, over SSE |
| Auth / IDP / headers / Firebase / MQTT | unchanged |

**The five new routes:**

| Method | Route |
|---|---|
| `GET` / `POST` | `api/v2/public-api-keys` |
| `DELETE` | `api/v2/public-api-keys/{id}` |
| `GET` | `api/v1/vehicle-information/{vin}/battery-health` |
| `GET` | `api/v2/garage/vehicles/{vin}/users/guests/invitations` |
| `POST` | `api/v2/predictive-maintenance/vehicles/{vin}/predictions/{type}/reset` |

`public-api-keys` is the app minting keys for a caller that is not the app —
the first sign of a first-party public API surface, and worth watching whatever
we do with it.
