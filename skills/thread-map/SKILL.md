---
name: "thread-map"
description: "Segment a long conversation or transcript into a navigable map of thematic segments, pivots, and continuity warnings, plus what to carry into a new thread. Use when the user invokes ThreadMap or TokenMap, or wants to reconstruct, navigate, or hand off a long chat."
---

# ThreadMap

## Purpose
Build a cognitively meaningful map of a chat log or conversation for navigation, reconstruction, and thread stitching. The source also calls this TokenMap Analyzer.

Use a sibling skill instead when the user wants the method behind an exchange rather than its map (meta-distill), or wants to know which paths the conversation never explored (negative-space, which can take a ThreadMap as input).

## Invocation
If invoked with no log, map the current conversation if it is long enough to have segments; otherwise ask for the transcript or excerpt and an optional objective (e.g., "highlight major shifts in reasoning," "prepare a handoff"). Default output is the table plus the narrative outline; add the stitching map when the user is moving to a new thread.

## Inputs
Conversation log or selected excerpts (Markdown, CSV, or text). Optional target question or stitching goal.

## Workflow
1. Identify chronological and logical boundaries: topic changes, decision points, changes in intent, correction loops, and reasoning shifts.
2. For each section, name a **title**, **purpose**, **important claims/decisions**, and **links to adjacent sections**.
3. Give lengths only when measured or soundly approximated (e.g., word count × ~1.3 for tokens), and label approximations as such. Do not claim to detect inaccessible model context or hidden memory loss.
4. Identify direct evidence of context loss, confusion, or unaddressed transitions (a dropped requirement, a contradicted earlier decision, a question never answered). Distinguish such evidence from speculative 'fade zones'.
5. Output a navigable table and a human-readable narrative outline.
6. Optional: create a stitching map showing what context must be transferred to a new thread or skill.

## Output
| Segment | Approx. length (if available) | Title | Cognitive purpose | Continuity warning |
Follow with **narrative reconstruction** and **context to carry forward**.

## Checks
Do not pretend to see transcript segments that were not supplied. Mark uncertain segmentation and inferred links.

## Provenance
Adapted from the user's original QAT-Kit prompt: https://drive.google.com/file/d/1L_nfLfLvaZBjSBkFC4Dyuk_QOlR2YmBw/view?usp=drivesdk. This is a portable adaptation, **not** a verbatim reproduction. The original's ~1.5k-token fade threshold was dropped because current models don't degrade at that length; continuity warnings are now evidence-based. Added in review: invocation behavior, sibling pointers, and the token-approximation rule.