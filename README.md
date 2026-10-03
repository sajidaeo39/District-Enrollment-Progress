# District Enrollment Progress

District-level Punjab enrollment progress dashboard using the live SIS PESRP gender-summary endpoint and the supplied district baseline/target workbook.

## Automatic update
GitHub Actions runs daily at **08:00 Pakistan time (03:00 UTC)** and can also be started manually from **Actions → Update District Enrollment Dashboard → Run workflow**.

Each run:
1. Creates a fresh SIS session and CSRF token.
2. Queries the SIS district enrollment endpoint for all 40 districts.
3. Reads the district baseline/target CSV.
4. Calculates current enrollment, increase, progress and target attainment.
5. Writes `data/district_progress.json`.
6. Commits the refreshed JSON and deploys the site to GitHub Pages.

No SIS session cookies or CSRF tokens are stored in the repository.

## Source
SIS endpoint: `https://sis.pesrp.edu.pk/dashboard_revamp/get_gender_summary_pie`

The district IDs used by the updater follow the 40-district category order supplied from the live SIS response.


Last updater fix: district names are matched from the SIS live district summary response rather than numeric district IDs.
