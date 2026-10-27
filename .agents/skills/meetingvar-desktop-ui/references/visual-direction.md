# Visual direction

This is a practical visual direction for the meetingVAR desktop interface, not a claim that one palette suits every person.

## Color and type

| Role | Starting value | Use |
| --- | --- | --- |
| Primary blue | `#2563EB`; hover `#1D4ED8` | Main action, selected navigation, focus |
| Violet accent | `#6D28D9`; tint `#F5F3FF` | Project-understanding features, small accents |
| Page background | `#F5F7FC` | Reading surface behind white cards |
| Card background | `#FFFFFF` | Main content panels |
| Supporting text | `#6B7280` | Helper text only after checking contrast on its actual background |
| Success / warning / error | Green / amber / red semantic colors | Pair color with a clear label or icon |

Use roughly 16px bold section headings, 14px body text, and 12px helper text with about 1.5 line height. Use a 4px spacing grid; 8px and 16px should cover most gaps. Keep Windows-friendly system font fallbacks, including Segoe UI and Microsoft YaHei.

Do not assume the listed supporting-text color passes contrast on every tinted surface. Check each rendered foreground/background pair. For stronger accessibility, also check focus visibility, Windows high-contrast mode, and reduced motion.

## Information and actions

- Keep primary content and the next safe action visible without forcing users to read repeated explanation paragraphs.
- Put optional explanation in an `(i)` control with hover, keyboard focus, click, and Escape support. Do not hide a material data-egress or cost notice inside a tooltip.
- Use primary, secondary, and quiet button styles consistently. Keep related controls on one row at desktop widths and provide a deliberate wrap or stack at narrower widths.
- Join a path input and its read button edge-to-edge, with matching height and no gap. Preserve the original Windows path string, including backslashes, spaces, and Chinese characters.
- Represent chunk counts, coverage, and elapsed-time summaries as compact badges or grouped status values. Use alert banners for recoverable errors and put the retry beside the error when safe.
- Keep long diagnostics, raw terms, and secondary details collapsed until requested. Never let a collapsed message hide a blocked main action without showing why.

## Interaction states

- Show loading on the operation that is actually running; prevent duplicate starts and conflicting project changes.
- When retrying, preserve the original scope and parameters, such as the five-chunk sample versus the full-project run.
- Navigation may focus the relevant control but must not start a request or incur model/API usage on its own.
- Show a completed step only after its matching operation succeeds. If the user changes a confirmed selection, invalidate the confirmation state.
- Respect `prefers-reduced-motion`; transitions should be brief and never carry essential meaning by themselves.
