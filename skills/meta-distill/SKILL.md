---
name: "meta-distill"
description: "Analyze a conversation (by default, the current one) as a reusable prompting or cognitive workflow: what made it work, the tactics used, a reusable method, who contributed what, and abstraction drift. Use when the user invokes MetaDistill or asks to turn an exchange into a reusable prompt or method."
---

# MetaDistill

## Purpose
Analyze a preceding dialogue as a PromptCraft artifact to extract reusable cognitive methods rather than merely summarizing its subject.

Use a sibling skill instead when the user needs the conversation's structure mapped or handed off (thread-map), or wants a single text's prose evaluated (frame-shift).

## Invocation
If invoked with no excerpt, analyze the current conversation up to the invocation. If the conversation is too short to have a method in it, say so and ask which exchange to analyze. If the user names an objective (e.g., derive a reusable prompt), lead with that output.

## Inputs
Conversation or excerpt (default: the current conversation); optional objective (e.g. derive a reusable prompt or process description).

## Workflow
1. Identify what made the exchange effective or ineffective: structure, pacing, tone, and alignment with the original goal.
2. Name recognizable tactics: recursion, role-shifting, metaphors, context anchoring, dialectic, or other actually-observed approaches.
3. Extract a reusable method in **prompt form** or as a concise **process description**.
4. Identify plausible uses of the method in other contexts (optional).
5. Assess whether this meta-prompt improves clarity or causes abstraction drift, and name one concrete adjustment.
6. Perform the source's **Cognitive Ownership Checkpoint**:
   - Summarize what was constructed.
   - Distinguish user-originated elements, model-originated suggestions, and joint refinement whenever the record supports attribution.
   - Call out unsupported leaps that require re-expression or validation.

## Output
**Object of analysis**, **observed successful tactics**, **reusable method**, **ownership/lineage**, **abstraction-drift check**, **one refinement**.

## Checks
Do not claim specific authorship or thought lineage without evidence in the provided dialogue. Distinguish extraction from new synthesis. When analyzing a conversation you took part in, don't credit yourself for ideas the user introduced, or vice versa; quote the turn where an idea first appears when attribution matters.

## Provenance
Adapted from the user's original QAT-Kit prompt: https://drive.google.com/file/d/1AYC7JIpEg-F06h-CPatTItIqBPB3UCli/view?usp=drivesdk. This is a portable adaptation, **not** a verbatim reproduction. Added in review: invocation behavior, sibling pointers, and the self-attribution check.