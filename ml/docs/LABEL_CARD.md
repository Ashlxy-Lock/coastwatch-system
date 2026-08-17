# CoastWatch ImpactNet v2 Label Card

Status: the code supports an auditable event catalogue; no reviewed real catalogue
has yet been supplied.

## Confidence classes

| Code | Meaning | Main confirmed-impact hazard head |
|---|---|---|
| A | exact or sufficiently precise time/location, reliable impact evidence, human reviewed | positive |
| B | multiple consistent evidence sources and high confidence | configurable positive |
| C | official warning without confirmed impact | auxiliary warning head only |
| U | insufficient information or unresolved evidence | masked |
| N | adequate coverage and affirmative evidence that no impact occurred | clean-negative candidate |

Absence of a record is not automatically `N`.

## Time precision

- `exact_hour`: eligible for onset hazard training.
- `interval`: eligible only under configured interval-width/soft-label rules.
- `date_only`: event-level evidence; never assigned an arbitrary positive hour.
- `unknown`: masked from onset training.

For prediction time `t`, an event onset at future hour `j` creates a positive
conditional-hazard target only when the event is not yet active and the confidence
is allowed. Samples inside `[onset, end]` are masked from the primary onset loss.

## Review requirements

Each event stores its event/storm group, zone, onset/peak/end, evidence booleans,
severity, warning severity, primary source, complete source references, reviewer
notes, and audit timestamps. Changes create a new label version; they do not silently
overwrite the provenance of a completed run.

## Selection bias and limitations

Official warnings, news, and recorded outlines preferentially capture larger or
better-documented incidents. Unreported impacts and quiet false-warning periods may
be underrepresented. Reports must show event counts and storm-group bootstrap
intervals, and must set an insufficient-evidence warning when events are sparse.

