# District Enrollment Progress

District-level enrollment progress dashboard built from the supplied 2026-27 district baseline/target workbook.

**Live-source note:** the connected `okara-enrollment-live` repository is an Okara-only SIS feed. The dashboard therefore keeps all 40 district target rows, but only shows a current enrollment value for a district when the live source actually provides that district. Missing live districts are shown as **Awaiting live source**, not zero.

The workflow runs at **03:10 UTC (08:10 Pakistan time)**, after the source repository's 03:00 UTC update window, and also supports manual **Run workflow**.

Enable GitHub Pages at **Settings → Pages → Source → GitHub Actions**.