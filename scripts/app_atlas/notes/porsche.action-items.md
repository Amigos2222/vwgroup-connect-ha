| # | Item | Why | State |
|---|---|---|---|
| 1 | Request `CHARGING_SESSION_HISTORY` alongside the other measurements and keep the raw response | The PPA measurement list is opt-in per key — a key we never name is a key we never see, however well it is documented | open |
| 2 | Map `HistoryItem` to the last-session fields that already exist in the models | The landing fields are there; nothing fills them for Porsche today | open |
| 3 | Leave `DASHCAM` alone | It is local-network video against the car's own hotspot, not a cloud read — outside what this integration does | closed (won't do) |
| 4 | Leave `SPIDERMAP` alone for now | Isochrone polygons need a SPIN per call and have no sensible entity shape | deferred |
| 5 | Watch the pin set, not just the version | Pins went 10 → 11 with no host change; a pin rotation is the cheapest early warning that a host is moving | standing |
| 6 | Re-check the two flavors on every minor bump | Identical at 20.26.37 is a measurement, not a promise | standing |
