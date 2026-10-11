# Workspace selected-version relations (Issue #18)

Status: implementation design. The adopted [ADR-0006](../decisions/0006-common-record-contract.md),
[bounded retrieval contract](../CONTRACTS.md#a2-제한된-맥락기록근거-조회-kit-0110), and
[continuity proposal, section 5](../DESIGN-CONTINUITY-AND-REUSE.md#5-인사이트-중심-html과-점진적-공개) determine meaning.
This change adds no stored record kind, model tool, provider call, Report writer, or publication.

The analytical job is exploring a selected version's directed, potentially cyclic one-hop reference
neighborhood. ReadModel owns identity, direction, reference judgments, binding and qualification.
The existing CLI/SDK relation read and Workspace use the same row selection. The renderer uses
existing offline HTML, DOM and decorative SVG; no layout library, network fetch, server or CDN is
needed. The graph is a stable incoming / selected / outgoing arrangement, ordered by source record
and reference position. Labels wrap in real DOM boxes; connectors use measured box boundaries.
A labeled text list carries every displayed relation, source location, basis, state, binding and
confirmation. Pagination preserves total/omitted counts and offers every remaining relation.
The graph is bounded to 18 neighboring boxes and one selected box per page; dense neighborhoods use continuation pages.
Neither run-level references nor attached data establish row-level or function-level lineage.

Each selected event, exact run or Visualization version, preserved CONTEXT version and referenced
query identity gets a deterministic static page. A link names the query identity already used by
retrieval, never a guessed current version. Unbound, missing, changed and unknown references remain
qualified; only resolved canonical targets offer a historical content link. Explicit **View latest**
links are separate. Raw authored Unicode and historical bytes remain unchanged. Analysis asset
rendering remains read-only and neither scores nor publishes suggestions.

The URL codec stores section, page-local kind filter, retired toggle, source revision, selected
identity, origin card and pixel offset relative to that card. Browser history holds the same state;
session storage is optional and cannot be the only recovery path. Navigation carries one validated
origin snapshot, without recursive nesting. Return links accept only generated Workspace HTML paths.
Invalid URL state is ignored with a visible notice. A vanished card/selection is reported and never
silently replaced by its newest version. A rebuilt source preserves the identity while visibly
naming the changed basis. Back/forward, refresh and explicit return restore selection/filter/card
position, including when browser storage is unavailable.

Portrait 360–430 px leads with the selected version and relation list; the wider diagram is a
secondary disclosure. Desktop shows the same diagram and list. Native scrolling owns touch and
wheel gestures; no drag/pinch interception or animated layout. Links, disclosures and filters use
native keyboard behavior, visible focus, 44 px primary hit areas, Escape to return, and Reset view.
No essential value depends on hover. Reduced motion disables smooth scrolling. Neutral borders
encode context; blue indicates the selected/focused item; amber highlights unresolved states;
red names missing/changed evidence. Text labels repeat all color meaning.

The first index contains bounded slices of records, visualizations, runs and feedback. Offline list
pages offer full continuation and state the page-local filter scope. First HTML size, full rebuild
cost and record-scale behavior are measured with the existing S/M generator. HTML/source/state-code
checks are distinct from actual UI evidence. Actual file URL rendering, keyboard, mobile and owner
Q6 task checks remain NOT_RUN until the owner performs the concrete artifact checklist; Issue #18
cannot be Done or merged on source checks alone.
