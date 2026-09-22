# Responsive UI review — 22 September 2026

## Changes and root causes

| Issue | Cause | Correction |
| --- | --- | --- |
| Upcoming Payments touches chart cards | Optional dashboard sections owned inconsistent margins; reveal animation translated cards into the next section | One dashboard grid owns a 24px section gap; dashboard reveal fades without translation |
| Doughnut chart looks small and label drifts | Canvas forced to 220px; label positioned at an arbitrary percentage independently of legend wrapping | Responsive canvas and label anchored to the rendered arc center on draw/resize |
| Narrow loan/marketplace cards overflow | Grid minimums of 320/340px exceeded available content width | Grid tracks shrink to available width; long labels wrap |
| Dashboard loan rows overflow | Balance, progress bar and percentage forced onto one line | Rows wrap with aligned balance/progress groups |
| Filters overflow on narrow phones | Shared search field had a 280px minimum inside padded cards | Inputs and filter groups can shrink to their containers |
| Landing headings render side by side | Shared `.section-header` class was later redefined as a flex row for app cards | Landing section headers explicitly keep stacked text |
| Landing mobile menu inaccessible | Toggle lived inside the action container hidden on mobile; no menu handler | Toggle remains visible; collapsible navigation, Escape and resize handling; tablet breakpoint at 1024px |
| Loan action buttons / section buttons misaligned | Separate Pay EMI row and fixed 660–770px gaps | Wrapping toolbar and flex section headings; consistent button heights |
| Forms, tables and user administration inconsistent | Inline desktop column counts, intrinsic widths, duplicated admin sidebar | Responsive grid hooks, local table scrolling, shared application shell |
| Mobile app navigation awkward | Drawer lacked complete close/focus handling | Close button, overlay, Escape, focus containment and expanded state |

Implementation is in `static/css/responsive.css`, loaded after page styles, with
small template and JavaScript changes. No financial calculations or database
schema were changed for this UI review. Tables intentionally scroll inside their
containers on narrow screens rather than shrinking financial data to unreadable text.

## Verification

Local preview: `http://127.0.0.1:8011/`, using `manage_local.py` and isolated
`local.sqlite3`. Production settings/database were not used. No commit or push.

- Admin: 23 routes × 7 viewport widths = 161 layout checks.
- Non-staff populated loan account: 19 routes × the same widths = 133 checks.
- Widths: 320, 390, 600, 768, 1024, 1280 and 1920px, height 900px.
- Public landing/login/register and four disbursement/delete confirmation routes:
  63 additional checks including 800×400 landscape and 1025px breakpoint edge.
- Initial failures reproduced on narrow cards/filters and the public landing page.
  Fixes were rechecked. The 800px landing navigation issue was found in the extra
  matrix and corrected by extending the menu breakpoint to 1024px. A final
  nine-width landing-page pass confirmed headings and navigation fit without
  page-level horizontal overflow.
- Visual inspection: populated dashboard card spacing, chart sizes and center
  label on desktop; stacked phone dashboard in light/dark themes; mobile landing menu.
- Interaction checks: mobile drawer open/Escape close; close-loan dialog fits a
  390×600 viewport and dismisses with Escape; landing menu open/Escape close.
  No financial or delete confirmation was submitted.
- 34 Django regression tests passed; JavaScript syntax and `git diff --check` passed.

Authenticated routes include dashboards, loans/list/create/edit/detail/compare,
disbursements/list/create, schedules, ledger, payments, prepayments, documents,
notifications, support/list/create, settings, marketplace and profile setup.
Admin coverage additionally includes users, banks, reports and activity logs.
All repository HTML templates were inspected. Support ticket detail has no local
fixture and was reviewed in source, not with a populated browser conversation.

These checks establish representative responsive coverage, not a claim that every
browser, device, dataset or possible resolution has been tested. Real Safari/iOS,
Android devices and unusual long uploaded content remain useful release checks.
CDN fonts/icons/Chart.js require network access.

## Repeatable manual check

1. Start with `manage_local.py`; use fictional accounts from the ignored local
   credentials file. Do not point testing at production.
2. Visit each route above at phone, tablet and desktop widths. Check that headings,
   actions, form fields and cards fit; only table containers should scroll sideways.
3. Scroll the populated dashboard to Upcoming Payments and charts. Check the 24px
   section gap, one-column phone charts, and doughnut label centered in its hole.
4. Resize through 640, 768 and 1024px. Open/dismiss the mobile navigation using
   keyboard and pointer; confirm controls remain reachable in landscape.
5. Open the close-loan dialog and cancel. Inspect create/edit and validation states
   without submitting payments, closures or deletion.
6. Run `../venv/bin/python manage_local.py test`, `node --check static/js/main.js`
   and `git diff --check` after changes.
