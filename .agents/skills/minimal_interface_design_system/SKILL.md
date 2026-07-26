---
name: Minimal Interface Design System
description: Rules and guidelines for designing or reviewing professional, minimal, high-clarity digital interfaces (dashboards, admin panels, dev tools, SaaS).
---

# Minimal Interface Design System

## Purpose
Use this skill to design or review professional, minimal, high-clarity digital interfaces.

The goal is not to create decorative visuals. The goal is to create an interface that is readable, consistent, accessible, and easy to operate under high information density.

This skill should be used when designing dashboards, admin panels, developer tools, SaaS interfaces, control panels, form-heavy products, data-heavy pages, or any interface where clarity and trust matter more than visual spectacle.

## Core Philosophy
Design is an information-ordering system.

Every visual decision must serve one of four purposes:
1. Clarify hierarchy.
2. Reveal state.
3. Guide action.
4. Reduce cognitive noise.

Do not add color, motion, shadows, icons, gradients, or decorative elements unless they improve one of these four purposes.

The interface should feel calm, precise, neutral, and durable.

## Design Principles

### 1. Restraint First
Use a minimal visual language.

Prefer:
- Neutral surfaces
- Clear typography
- Strong contrast
- Generous whitespace
- Thin borders
- Sparse accent color

Avoid:
- Decorative gradients
- Excessive shadows
- Unnecessary animation
- Too many colors
- Competing button styles
- Visual effects without functional meaning

The interface should look quiet, not empty.

### 2. Color Must Carry Meaning
Never use color only because it looks good.

Every color must have a role.

Recommended semantic roles:
- Primary text
- Secondary text
- Muted text
- Page background
- Card background
- Border
- Focus
- Success
- Warning
- Error
- Info
- Selected state
- Hover state
- Active state
- Disabled state

Use neutral colors for structure and hierarchy. Use accent colors only for state, focus, links, and the most important action.

Do not signal state with color alone. Pair color with text, icon, label, or shape.

### 3. Use Token-Based Design
All visual values should come from tokens.

Do not manually invent random colors, font sizes, spacing, radii, or shadows inside individual components.

Define reusable tokens for:
- Color
- Typography
- Spacing
- Radius
- Border
- Shadow
- Motion
- Component sizes
- Interaction states

Example token categories:
```yaml
colors:
  text-primary
  text-secondary
  text-muted
  background-primary
  background-secondary
  border-default
  border-hover
  accent
  success
  warning
  error

spacing:
  4
  8
  12
  16
  24
  32
  40
  64
  96

radius:
  small
  medium
  large
  full

typography:
  heading-large
  heading-medium
  heading-small
  body
  label
  caption
  mono
```
Tokens are the source of truth. Components consume tokens. Pages compose components.

### 4. Establish a Clear Type System
Use typography to create hierarchy before using color or decoration.

Recommended type roles:
- Heading: page titles and section titles
- Body: multi-line explanatory text
- Label: short interface text, metadata, table headers, form labels
- Button: action text
- Mono: code, logs, IDs, numerical data, tabular figures

Use no more than two font families:
- Sans for interface and prose
- Mono for code, data, logs, and aligned numbers

Use no more than two or three font weights in one view.

Large headings may use tighter letter spacing. Body text should use comfortable line height.

### 5. Use Spacing as Structure
Use a consistent spacing scale.

Recommended rhythm:
- **4px**: micro-adjustments
- **8px**: spacing inside tight groups
- **12px**: compact component spacing
- **16px**: spacing between related elements
- **24px**: card padding or medium section spacing
- **32px**: separation between major groups
- **40px**: section rhythm
- **64px**: large page separation
- **96px**: hero or major layout spacing

Use spacing to show relationships:
- Items close together belong together.
- Items far apart are separate concepts.
- Cards need internal breathing room.
- Sections need stronger separation than rows.

Do not use borders when spacing alone can communicate grouping.

### 6. Build Hierarchy with Surfaces and Borders First
Use shadows sparingly.

Preferred hierarchy order:
1. Typography
2. Spacing
3. Surface tone
4. Border
5. Subtle shadow
6. Accent color

Cards should usually use a neutral background, light border, and minimal or no shadow.

Popovers, menus, and dialogs may use stronger elevation, but still keep shadows subtle.

Do not create heavy floating interfaces unless the component must visually sit above the page.

### 7. Keep Shapes Consistent
Use a small radius system.

Recommended radius roles:
- Small radius: buttons, inputs, small cards
- Medium radius: menus, popovers, dialogs
- Large radius: large containers or full-screen surfaces
- Full radius: pills, avatars, circular controls

Do not mix sharp corners and highly rounded corners in the same view without a clear reason.

One view should feel like it belongs to one shape family.

### 8. One Primary Action Per View
Each page or panel should have one visually dominant action.

Use action hierarchy:
- **Primary**: the single most important action
- **Secondary**: important but not dominant action
- **Tertiary**: low-emphasis action
- **Destructive**: dangerous or irreversible action

Do not place multiple primary buttons in the same decision area.

Button labels must be specific.

Prefer:
- Save Changes
- Delete File
- Create Workspace
- Invite Member
- Export Report

Avoid:
- OK
- Confirm
- Submit
- Yes
- Continue

A user should understand what will happen before clicking.

### 9. Interaction States Must Be Explicit
Every interactive element needs visible states:
- Default
- Hover
- Active
- Focus
- Disabled
- Loading
- Error, if applicable

Focus state must be visible for keyboard navigation. Never remove outlines unless replacing them with an accessible visible focus ring.

Disabled state should reduce emphasis but remain legible.

Loading state should use direct language, such as:
- Saving…
- Uploading…
- Loading…
- Generating…
- Deleting…

Use the ellipsis character `…`, not three periods.

### 10. Motion Must Explain Change
Motion is not decoration.

Use motion only when it helps the user understand:
- Something appeared
- Something disappeared
- Something changed position
- A process started
- A process completed
- A hierarchy changed

Recommended motion rules:
- Instant interaction is often best.
- State changes should be short.
- Popovers and menus should feel fast.
- Dialogs and overlays may be slightly slower.
- Avoid looping animation unless showing an ongoing process.
- Respect reduced-motion preferences.

If motion does not clarify state, remove it.

## Content Rules
Copy is part of the interface.

Write interface text that is short, specific, and operational.

### Buttons
Use verb + noun.
- **Good**: Create Project, Delete Member, Save Settings, Export Data
- **Bad**: Confirm, OK, Submit, Yes, Next

### Toasts
Name the thing that changed.
- **Good**: Project deleted, Settings saved, Export started
- **Bad**: Successfully deleted the project., Your action was completed successfully., Done.

Do not use “successfully” unless there is a strong reason.

### Errors
Write errors as: **What happened. Why it happened. What to do next.**
- **Good**: Upload failed. File exceeds 50 MB. Compress it or choose a smaller file.
- **Bad**: Something went wrong.

### Empty States
Point to the first useful action.
- **Good**: No files yet. Upload a file to get started.
- **Bad**: Nothing here.

### In-Progress States
Use present participle + ellipsis.
- **Good**: Saving…, Loading…, Uploading…, Generating…
- **Bad**: Please wait, Working, Processing...

Success!

## Component Guidelines

### Buttons
Default button heights:
- Small: 32px
- Medium: 40px
- Large: 48px

Use small buttons in dense toolbars and tables.
Use medium buttons for most interfaces.
Use large buttons for primary mobile actions or high-emphasis calls to action.

Primary buttons should be visually strong but rare.
Secondary buttons should be available without competing with the primary action.
Tertiary buttons should feel lightweight.
Destructive buttons should be visually distinct and used only for dangerous actions.

### Inputs
Inputs should be calm, legible, and predictable.

Required states:
- Default
- Hover
- Focus
- Disabled
- Error

Input labels should be clear. Helper text should explain constraints before the user fails.
Error messages should appear close to the field and explain the fix.

### Cards
Cards should group related information.

Use cards when the content needs a boundary.
Do not over-card the interface. Too many cards create visual fragmentation.

A good card usually contains:
- A clear title
- Optional metadata
- Main content
- Optional action area

Card padding should be consistent across the page.

### Tables
Tables should prioritize scanning.

Use mono typography for aligned numbers, IDs, metrics, and code-like values.
Keep row height consistent.
Use muted text for secondary metadata.
Avoid excessive grid lines. Use spacing and light dividers.

Important table states:
- Empty
- Loading
- Error
- Selected
- Hover
- Sorted column

### Badges and Status Labels
Use badges for compact state communication.

Every badge should include text. Do not rely only on color.
- **Good**: Active, Failed, Pending, Draft, Archived, Warning
- **Bad**: Colored dot only, Icon only, Unlabeled color chip

### Dialogs
Dialogs interrupt the user. Use them only when interruption is necessary.

A dialog should contain:
- Clear title
- Concise explanation
- Specific primary action
- Clear cancel or close action
- Destructive styling if the action is dangerous

Do not use vague confirmation text.
- **Good**:
  > **Delete workspace?**
  > This will remove all files and settings in this workspace. This action cannot be undone.
  > [Cancel] [Delete Workspace]
- **Bad**:
  > **Are you sure?**
  > [No] [Yes]

## Layout Guidelines
Use a centered content column unless the product requires full-width data views.

Recommended max widths:
- Reading content: 720px to 840px
- Standard app page: 960px to 1200px
- Data-heavy workspace: full width with controlled padding

Mobile and desktop layouts should share the same hierarchy, even if the arrangement changes.
Do not make mobile a reduced-quality version of desktop. Simplify, prioritize, and preserve the main action.

## Accessibility Requirements
- Always maintain accessible contrast.
- Body text should meet WCAG AA contrast.
- Interactive elements must be keyboard accessible.
- Focus states must be visible.
- Do not communicate meaning through color alone.
- Use semantic HTML when applicable.
- Use labels for form controls.
- Use aria attributes only when semantic HTML is insufficient.
- Honor reduced-motion preferences.

## Design Review Checklist
Before finalizing a design, check:
1. Is there exactly one primary action in the main decision area?
2. Are colors semantic rather than decorative?
3. Are typography roles consistent?
4. Are spacing values from the spacing scale?
5. Are component sizes standardized?
6. Are hover, active, focus, disabled, and loading states defined?
7. Are errors specific and actionable?
8. Are empty states useful?
9. Are numbers, logs, IDs, and code-like values using mono typography?
10. Is state communicated with text, not color alone?
11. Is motion necessary?
12. Are shadows subtle?
13. Are corners consistent?
14. Is the page readable at a glance?
15. Can a user understand the next action without guessing?

## Output Format When Using This Skill
When asked to design an interface, produce:
- Design intent
- Information hierarchy
- Layout structure
- Component list
- Token recommendations
- Interaction states
- Copy examples
- Accessibility notes
- Review checklist

When asked to review an interface, produce:
- What works
- What creates noise
- What breaks hierarchy
- What breaks consistency
- What should be simplified
- Specific improvements
- Revised component or layout recommendation

When asked to create implementation guidance, produce:
- Token definitions
- Component rules
- Layout rules
- State rules
- Copy rules
- Do / don’t examples

## Do Not
- Do not create decorative UI for its own sake.
- Do not use random colors outside the token system.
- Do not use multiple competing accent colors.
- Do not remove focus states.
- Do not rely only on color to show status.
- Do not use vague button labels.
- Do not write generic errors.
- Do not overuse shadows.
- Do not mix too many radii.
- Do not use animation unless it explains change.
- Do not make the page visually impressive at the cost of clarity.

## Final Standard
A successful interface should feel:
- Clear
- Quiet
- Fast
- Consistent
- Trustworthy
- Accessible
- Operational
- Low-noise
- Easy to scan
- Hard to misuse

The best version of this design system should not draw attention to itself. It should make the user understand the product faster.
