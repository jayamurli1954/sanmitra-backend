# Legacy Modularization + Duplication Handoff (2026-03-26)

## Completed now (LegalMitra)

- Modularized frontend index assets:
  - Extracted inline CSS to `external-repos/LegalMitra/frontend/css/index-main.css`.
  - Extracted main inline JS to `external-repos/LegalMitra/frontend/js/index-main.js`.
  - Extracted cases/news module to `external-repos/LegalMitra/frontend/js/legal-feed.js`.
- Extracted large policy modal content into separate files:
  - `external-repos/LegalMitra/frontend/legal/disclaimer.html`
  - `external-repos/LegalMitra/frontend/legal/privacy.html`
  - `external-repos/LegalMitra/frontend/legal/terms.html`
- `index.html` line count reduced:
  - before: 3903
  - after: 441
- Post-modularization duplicate scan in LegalMitra:
  - only remaining duplicate file: `external-repos/LegalMitra/backend/app/services/document_storage.py` (`safe_sort_key` appears twice).

## Remaining oversized files (>1200) for tomorrow (non-LegalMitra)

- `external-repos/GharMitra/web/src/screens/SettingsScreen.jsx` (4025)
- `external-repos/GharMitra/web/src/screens/AccountingScreen.jsx` (3448)
- `external-repos/GharMitra/src/screens/settings/SocietySettingsScreen.tsx` (2150)
- `external-repos/GharMitra/web/src/screens/MembersScreen.jsx` (2034)
- `external-repos/GharMitra/web/src/screens/ReportsScreen.jsx` (1753)
- `external-repos/MandirMitra/frontend/src/pages/Sevas.js` (1603)
- `external-repos/GharMitra/src/screens/accounting/AddTransactionScreen.tsx` (1533)
- `external-repos/GharMitra/web/src/screens/MaintenanceScreen.jsx` (1515)
- `external-repos/MandirMitra/frontend/src/pages/Donations.js` (1268)
- `external-repos/MandirMitra/frontend/src/pages/Settings.js` (1224)

## Highest-value duplicate hotspots for tomorrow (strict scan)

- `external-repos/MandirMitra/backend/panchang_service_current_backup.py`
  - `time_to_minutes` (x5), `minutes_to_time` (x5), `jd_to_time_string` (x2)
- `external-repos/MandirMitra/backend/panchang_service_my_changes_backup.py`
  - `time_to_minutes` (x5), `minutes_to_time` (x5), `jd_to_time_string` (x2)
- `external-repos/MandirMitra/backend/panchang_service_backup.py`
  - `time_to_minutes` (x4), `minutes_to_time` (x4), `jd_to_time_string` (x2)
- `external-repos/GharMitra/backend/app/utils/encryption.py`
  - `encrypt` (x2), `decrypt` (x2)
- `external-repos/GharMitra/backend/app/routes/settings.py`
  - `format_flat_number` (x2)
- `external-repos/InvestMitra/backend/server.py`
  - `register_options` (x2)

## Suggested order tomorrow

1. MandirMitra frontend page splits: `Sevas.js`, `Donations.js`, `Settings.js`
2. GharMitra web screen splits: `SettingsScreen.jsx`, `AccountingScreen.jsx`, `MembersScreen.jsx`
3. Deduplicate shared backend helpers in backup-heavy files and utility modules
4. Add a simple line-limit + duplicate-check script in repo tooling to prevent regressions
