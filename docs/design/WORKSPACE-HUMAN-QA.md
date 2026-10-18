# Issue #18 owner Workspace QA

This is a task checklist for the owner ([operations policy](../PROJECT-OPERATIONS.md#4-issue에서-pr까지)).
Source/unit checks cannot establish actual file URL rendering, keyboard, mobile or the Q6 tasks.
The implementation handoff supplies the exact clean source commit, synthetic Workspace paths,
fixture digests and selected record IDs. Record browser/OS/version, viewport, source commit,
fixture/source revision, task outcome and screenshots when this checklist is run.

1. Open the supplied `qa-S/workspace/index.html` as a local file. Keep network disconnected.
   Use Insights to find the supplied original finding. Its Unicode and HTML-like text must remain
   authored text; the correction retains the original. Follow its Relations link (one link per item
   since kit 0.17.0; Evidence/Related items/History before).
2. In the original finding's small graph and relation list, verify direction, basis, binding,
   confirmation and source location agree. Open the supplied exact historical Visualization.
   Inspect the attached table and recorded code; use the way back (named by where it goes since kit
   0.17.0) and browser Back/Forward.
   The same finding/card, filter, version and position must return. View latest is an explicit
   different action. Follow the correction and return to the original finding's retained history.
3. From Browse all, advance to a continuation list, choose a kind (its own list page since kit 0.17.0) and scroll to a card.
   Open details, refresh details, return, and use Back/Forward through kind list changes. Repeat with storage disabled. The URL carries the
   origin state. Invalid saved state, a vanished card after rebuilding, and a changed source basis
   must produce a visible qualified notice instead of substituting the latest version.
4. From Data follow its Relations link to its recorded references and a related finding. In the S
   fixture, inspect version-unbound chart names, missing/changed data, ambiguous producer
   candidates and cyclic/forward references. Do not read an unbound/current link as old evidence.
   Browse all relation pages: shown/omitted totals and direction-filter page scope must be clear.
5. Complete the same route using only Tab, Shift+Tab, Enter, disclosure controls and Escape.
   Check focus visibility, accessible source/selection summaries, reset and return. No essential
   label, value or caveat may depend on hover, color or graph connectors. With JavaScript disabled,
   exact content links and the full paginated text relation list must remain readable.
6. At portrait 360, 390 and 430 px, confirm the selected version and relation list lead before the
   wide diagram/long stored record. Labels wrap without tiny text or page overflow. Native scroll
   works with touch; primary controls have comfortable hit areas. Check mobile landscape and
   increased text size. Reduced motion retains all relationships and controls.
7. Open the S and M scale artifacts. In M, use the second Data list to open a dataset beyond the initial 50.
   In Visualizations, use the retired visibility toggle on a continuation page and an exact card link. Record actual first-screen usable time and initial transfer
   size separately from generation time. Continue to old records outside the first bounded page;
   choose a historical version and return to the same original list/card. Check `display_safety`
   examples as text and compare source revision across relation/detail pages.
8. (kit 0.18.0, #98) On the Overview at 1366×900, the goal, the pinned key questions and the six status cells show on the first
   screen. Each cell says its own state and nothing adds them into one score; compare a cell's numbers with its source (the
   Insights badges, the Runs statuses, the Visualizations' input badges, the Evaluation rerun row). Open Analysis map: in S, the
   three main flows (k01/k02 → k03, s01 → s02 → s03, s04 → s05 → s06), the failed k03 run with the six insights citing it, the
   input changes, and in Needs attention and the folded smaller flows the file with two possible producers and the cycle.
   At 360/390/430 px, at increased text size and without JavaScript, the map reads as a list of boxes, each naming the files it
   writes and the box that reads them. Repeat in Korean.
9. (kit 0.19.0, #99) In M, open the run page of `delivery_delay 요약 차트`'s latest version: `Version 19 of 19`, the note that
   both its code and input changed, the strip and the list with v15–v18 and v1–v14 folded and the failed run of Sep 1 between them.
   Open the dataset page of `t10_delivery_delay` (14 versions from 19 writes, each with the runs that read it, version 14 read by
   none yet), the script page of `s10_delivery_delay.py` from the run page's `code version` link (3 code versions: v2 failed, then
   `v1 again`, and v3's diff), and the Context page (13 versions, the pinned sections last changed in version 2). Check that the
   numbers agree across the gallery card, the run pages and the dataset page, and that each list reads without JavaScript and at
   360/390/430 px. Repeat in Korean.

The agent records measured HTML bytes and Workspace generation/cache-rebuild costs separately.
The owner's actual-screen task results fulfill the remaining Issue #18 acceptance condition.
Any unexecuted task remains `not_run`; opening a PR, green CI or generated HTML is not that result.
