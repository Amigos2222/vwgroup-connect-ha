**20.26.31 (versionCode 183270) → 20.26.37 (versionCode 188460)**, verified by
walking the DEX with androguard (not a string grep) on both APKs. Holds for
`de.porsche.one` too — see Flavors above.

| Area | 20.26.31 | 20.26.37 | Note |
|---|---|---|---|
| `MeasurementType` enum | 86 members | 87 members | `+CHARGING_SESSION_HISTORY` |
| `CommandType` enum | 61 members | 62 members | `+DASHCAM`, `+SPIDERMAP`, `−CS_VOICE_MIMIC` |
| Manifest permissions | — | `+ACCESS_LOCAL_NETWORK` | the only permission change; it is what the dashcam talks over |
| Certificate pins | 10 | 11 | one added, none removed; the 10 existing pins are byte-identical |
| VICI BFF host | present | removed | signup/profile traffic no longer routes through it |
| `TRUNK_UNLOCK` | present, not surfaced | present, user-facing | the command itself is unchanged in both builds |
| Auth stack | — | unchanged | Auth0, `identity.porsche.com`, `my.porsche.com`, PPA route shapes and `X-*` headers all identical |

**`CHARGING_SESSION_HISTORY` payload** — one `HistoryItem` per session:

| Field | Shape |
|---|---|
| `id` | int |
| `startChargingDateTimeWithOffset` / `endChargingDateTimeWithOffset` | ISO-8601 with offset |
| `plugInDateTimeWithOffset` / `plugOutDateTimeWithOffset` | ISO-8601 with offset |
| `netDurationS` | seconds |
| `chargeType` | `AC` \| `DC` \| `AWC` \| `UNKNOWN` |
| `averageChargingPowerkW` / `peakChargingPowerkW` | float, kW |
| `totalChargedEnergykWh` | float, kWh |
| `startSoC` / `endSoC` | int, percent |

The app tells users that history older than 28 days is unavailable, so do not
expect the backend to answer for a longer window.

**`SPIDERMAP`** is an isochrone command (reachable-range polygon) and carries a
SPIN in its payload, which puts it in the same authorisation class as the other
SPIN-gated commands rather than with the plain reads.

**Attribution caveat.** The North-American line jumped 20.26.31 → 20.26.37 while the rest-of-world line was already at 20.26.36. Cross-checked against `de.porsche.one` 20.26.36: the dashcam capability, `ACCESS_LOCAL_NETWORK`, push-category subscriptions, the user-facing tailgate unlock, pickup & delivery and the contract renames were all in 20.26.36 already, i.e. 20.26.32–20.26.36 changes that are merely new to the NA line. Genuinely new in 20.26.37 (absent from ROW 20.26.36 too): the charging-history screen and its `CHARGING_SESSION_HISTORY` fields `plugInDateTimeWithOffset`/`plugOutDateTimeWithOffset`/`startSoC`/`endSoC`, the `SPIDERMAP` command, POI-sync refusal reasons, and homescreen layout editing.
