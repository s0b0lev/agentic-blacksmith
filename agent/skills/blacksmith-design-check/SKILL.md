---
name: blacksmith-design-check
description: Use when comparing implemented UI screenshots against the referenced Figma frames through a Figma MCP server and fixing clear visual mismatches.
---

# Blacksmith: design check

You compare what was built with what was designed, fix the obvious gaps, and report
the rest. You are not redesigning anything.

## Read first

1. The issue and plan: collect every Figma link, file key, node id or frame name.
2. The screenshots in the shots directory and the verification report describing them.
3. The project's styling system: design tokens, theme files, shared components.

## Method

1. Check that a Figma MCP tool is available (the server the repository's `.mcp.json`
   declares). If not, write the report saying so, as the prompt describes, and stop.
2. Fetch only the referenced frames. Do not browse the whole file.
3. For each frame, find the matching screenshot (or take one with `$BLACKSMITH_BROWSER`).
   Compare in this order: presence of elements, layout and order, spacing, colours,
   typography, copy, icons and images.
4. Classify each difference:
   - clear mismatch: wrong token, missing element, wrong text, obviously wrong size;
   - judgement call: sub-pixel spacing, rendering differences, placeholder data.
5. Fix clear mismatches in code using the project's tokens and components, never
   hard-coded values when a token exists. Commit each fix; never push.
6. The preview is not redeployed during this stage; confirm fixes by reading the code.

## Report

Sections `## Compared`, `## Fixed`, `## Remaining`:
- Compared: frame (name or node id) against screenshot file.
- Fixed: mismatch and commit sha.
- Remaining: mismatch and why it is left (judgement call, out of scope, missing asset).

## Done means

- Every referenced frame appears under Compared.
- Clear mismatches are fixed and committed, or listed under Remaining with a reason.
- Tests still pass for any file you changed.

## When stuck

- No frames referenced: compare the overall page with the closest frame by name if
  obvious; otherwise report that nothing was referenced and stop.
- Figma calls fail or rate-limit: report what was compared so far and stop.
- Never print or store the Figma API key.
