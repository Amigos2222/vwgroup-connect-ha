This is the rest-of-world My Porsche build — the one EU owners install.
North America runs `com.porsche.one`, tracked on [`porsche.md`](porsche.md).

At **20.26.37** (versionCode 188460) the two are the same code: the DEX
string-pool diff is empty apart from the package-name strings and one
Porsche-Card product-availability path, and the Firebase config is identical.
Both are polled anyway, so a future divergence shows up as two different
version rows rather than as nothing at all.

The build string names the flavour: the mirror listing for this package reads
`20.26.36-row+186611`, where the North-American APK reads
`20.26.37-pcna+188460`. The atlas records only the dotted part — a suffixed
string does not compare against a plain one, and that comparison is what stops
a lagging mirror from walking the brand backwards.

Sources: APKMirror has no slug here (the Porsche slug it does have serves the
NA package, and reusing it would report an NA version as this brand's), and
Google Play publishes no version for either Porsche package. APKCombo is the
only source that answers, and it currently lags a release behind.
