| # | Item | Why | State |
|---|---|---|---|
| 1 | Probe `vehicle-information/{vin}/battery-health` on a tester car | It is a read, it is per-VIN, and battery health is the one number owners ask for that we cannot currently answer | open |
| 2 | Add `BATTERY_HEALTH_STATE` + `PUBLIC_API_KEY_MANAGEMENT` to the capability-id table | The table mirrors the app's enum; a missing id reads as "unknown capability" and unknown must never hide a control | open |
| 3 | Pick up `predictions[]` in the predictive-maintenance body | Additive field on a body we already parse — cheap, and it is where the per-service due dates moved | open |
| 4 | Leave the guest-invitation route alone | It writes to somebody else's access to the car; nothing about it belongs in an unattended integration | closed (won't do) |
| 5 | Watch `public-api-keys` | If this becomes a real first-party public API it changes what we should be building against, well before the mirror endpoints notice | standing |
| 6 | No auth work needed for 8.16.0 | IDP, headers and MQTT are unchanged — this bump breaks nothing we ship | closed |
