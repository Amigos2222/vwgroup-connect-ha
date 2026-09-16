My Porsche ships as two packages, and the atlas tracks both:

| Flavor | Package | Atlas page |
|---|---|---|
| North America (PCNA) | `com.porsche.one` | this page |
| Rest of world (EU included) | `de.porsche.one` | [`porsche_row.md`](porsche_row.md) |

At **20.26.37** (versionCode 188460) the two builds are the same code: a DEX
string-pool diff of both APKs comes back identical apart from the package-name
strings themselves and one Porsche-Card product-availability path, and the
Firebase config matches. So a finding verified on one flavor holds for the
other **at this version** — which is exactly why both are polled: the day the
builds diverge, the version pages diverge with them instead of the split
passing unnoticed behind a single row.
