# Punjab Enrollment Progress Dashboard

Punjab-wide school-level enrollment dashboard based on the supplied master CSV.

## Data rules
- Baseline values remain fixed from `data/master.csv`.
- 2026 male/female target increments remain fixed from `data/master.csv`.
- Male Current, Female Current and Total Current are fetched daily from SIS.
- Progress % = Total Current / (Male Target 2026 + Female Target 2026) × 100.
- The updater uses the same SIS session/CSRF and hierarchy endpoints used by the existing Okara collector.
- If any school fails, the run stops and the previous live dataset remains published.

## Required master file
Upload the supplied CSV as `data/master.csv`. Its expected columns include:
`District, Tehsil, Markaz, EMIS, School Name, Male Baseline, Female Baseline, 2026 Male target , 2026 female target`.

## GitHub Pages
After `data/master.csv` is present, run **Actions → Update Punjab Enrollment Dashboard** once. The same workflow then runs daily at 08:00 Pakistan time.

## Dashboard
Filters: District, Wing, Tehsil, Markaz, School/EMIS search.
