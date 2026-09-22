# Codex Workflow Rules

This file contains stable operating rules for this project.

It must be read before non-trivial work. It is not a historical diary. The working notebook is `docs/notebook.md`.

All internal Codex-facing documents must be written in English from now on. The product UI, user-facing labels, validation messages, and final app copy remain Italian unless the user explicitly decides otherwise.

## Role

Act as the project head engineer:

- read the code before deciding;
- explain important technical choices clearly;
- prefer clean, updateable, production-oriented solutions;
- do not present shortcuts as architecture;
- teach professional reasoning without bloating the chat.

## Document Split

Document structure:

```text
AGENTS.md
  -> short authoritative index

.agents/rules_workflow.md
  -> stable operating rules

docs/notebook.md
  -> working notebook: plans, decisions, doubts, attempts, closures

docs/packaging_plan.md
  -> future desktop packaging plan
```

Rules:

- stable principles belong here;
- project history, current plans, decisions, attempts, doubts, and closures belong in the notebook;
- future packaging strategy belongs in `docs/packaging_plan.md`;
- keep documents concise enough to be useful as working memory.

## Project Point Workflow

For structural work, packaging, runtime, input/output, persistence, user workflows, OCR queues, archives, or important refactors, follow the full workflow.

### 1. Theoretical Problem Definition

Define the problem as generally as possible before choosing a solution.

Avoid overly local framing such as:

```text
move this table
```

Prefer:

```text
separate operational data, archived data, and supervision views in a desktop management app
```

Goal: reveal sibling problems that would otherwise stay hidden.

### 2. Resolution Theory And Best Practice

Describe:

- how this class of problem is normally solved in modern apps;
- which workflows are robust;
- which mistakes to avoid;
- which tradeoffs matter;
- what is industry-standard and what is project-specific.

If a proposal is unusual or close to novel:

- state that clearly in chat;
- write criticisms, defenses, and abandonment criteria in the notebook.

### 3. Chosen Technical Approach

Classify the choice:

- **final choice**: suitable for the distributed app;
- **healthy incremental choice**: incomplete but grows toward the final architecture;
- **risky temporary choice**: unlocks a test but must be removed.

Default:

- prefer final or healthy incremental choices;
- do not use "beta" as an excuse for architectural debt;
- stop and warn the user if the current environment would force a worse solution.

### 4. Micro-steps

Before editing code, split the point into micro-steps:

- what changes;
- why it changes;
- which files/areas it touches;
- how it will be verified.

Update the notebook if the plan changes during the work.

### 5. Implementation

Code must be:

- clean;
- testable;
- not overly environment-dependent;
- compatible with Windows 10 and Windows 11;
- coherent with a serious desktop app;
- easy to update;
- free of hardcoded developer-machine paths;
- free of unnecessary duplication.

Prefer small real abstractions for:

- runtime paths;
- user file access;
- logs;
- launcher;
- import/export;
- reusable tables;
- save workflows.

### 6. Verification And Eyes On The Result

"Test" does not only mean pytest.

Use the right eyes for the risk:

- frontend build;
- frontend lint;
- typecheck;
- Python tests;
- diagnostic scripts;
- screenshots;
- created-file inspection;
- dry run;
- targeted manual verification;
- before/after comparison.

#### Cross-Boundary Integration Verification

When a change crosses application boundaries, validating each component in
isolation is not sufficient. Before implementation, map the complete runtime path
used by the user, for example:

```text
frontend
  -> HTTP transport
  -> distributed-app router or launcher
  -> backend handler
  -> persistent storage
  -> response
  -> frontend state
```

Include every relevant boundary in the verification plan, even when its file was
not directly edited. Common boundaries include the frontend, transport, local-app
router, backend, filesystem, startup process, and packaged runtime.

For every new or changed cross-boundary contract:

- verify component logic at the appropriate unit level;
- verify the real contract through the actual transport or adapter;
- exercise the path at least once through the same entry point used by the
  distributed application;
- compare development-server behavior with local-app behavior when they have
  different composition roots;
- prefer removing duplicated route or configuration declarations over adding
  endpoint-specific patches indefinitely.

Mocks are allowed for isolation, determinism, or protection of real user data, but
they create explicit blind spots. Every mocked check must record:

```text
verifies: <behavior covered by the check>
does not verify: <real components or boundaries replaced by mocks>
```

If a mocked boundary is part of the changed contract, add a separate unmocked
integration check for that boundary. Never report a mocked interaction test as a
complete end-to-end or local-app verification.

Screenshots verify visual results such as layout, overlap, readability, and active
states. They do not prove routing, persistence, response formats, or backend
correctness. Functional assertions and visual inspection are complementary and
must be reported separately.

Before closing the point, perform a contract-coverage review:

- list the runtime path that was exercised;
- list every mocked or bypassed boundary;
- confirm that each newly introduced contract was tested without that boundary
  being mocked;
- state precisely whether the result is a unit, integration, local-app, visual, or
  true end-to-end verification.

### 7. Critical Review

Before closing a point, ask:

- is the code simpler or more fragile?
- did we introduce unnecessary coupling?
- does the user see less complexity?
- is future updating easier?
- are user data protected?
- do logs/errors actually help?
- is there something to redo now instead of accumulating debt?

### 8. Closure

At the end of a point, write in the notebook:

- adopted solution;
- why it is satisfactory;
- checks run;
- residual limits;
- recommended next points.

In the final chat message, link the notebook section where the user should start reading.

## Desktop And User Data Rules

Primary architectural principle:

```text
application code != user data
```

Application code may be replaced on every update. User data must live outside the repository and must not be lost during rebuilds, updates, or reinstalls.

Expected runtime root:

```text
%LOCALAPPDATA%\EstrattoreScanFatture\
```

Primary safety rule:

- the app must never modify or delete files outside its own internal storage;
- when the user selects PDFs, folders, or Excel files, the app may read and copy them;
- every deletion must affect only internal copies created by the app.

## Packaging Direction

Do not rewrite the app as a native desktop app.

Chosen approach:

```text
Windows launcher
  -> starts local Python backend
  -> serves built React frontend
  -> opens a launcher-controlled app window
  -> stores persistent user data in user folders
```

External browser usage remains development/debug mode. The final version must control the app window, backend lifecycle, and OCR jobs.

## UI And Design Rules

For new or redesigned UI:

- follow `mystudio-platform` visual language when relevant;
- avoid duplicated information in the same view;
- separate navigation, process supervision, document archive, and data register;
- do not put operational functions inside historical pages if they belong to active workflows;
- large management tables must use coherent reusable infrastructure and virtualization;
- labels and user-facing texts must be Italian and non-technical.

## UI Check Protocol

After every non-trivial UI change, classify risk:

- **low**: text, labels, isolated CSS classes;
- **medium**: page layout, sidebar, modals, tables, stateful buttons;
- **high**: interactive workflows, saves, deletes, imports, OCR, navigation.

Minimum checks:

- low risk: `npm --prefix apps/viewer run build` + `npm --prefix apps/viewer run lint`;
- medium risk: build + lint + visual/Playwright check of touched UI surfaces, when available;
- high risk: build + lint + Playwright click-paths on touched flows, when available + relevant backend/frontend tests.

If lint fails due to pre-existing debt:

- report it explicitly;
- distinguish pre-existing errors from introduced errors;
- do not call the check passed;
- propose or open a dedicated cleanup point.

## Playwright Usage

Playwright is development tooling, not a runtime dependency.

It should live outside the final app, preferably in:

```text
tools/ui-checks/
```

Standard command:

```powershell
npm --prefix tools/ui-checks run check -- --url http://127.0.0.1:8765 --scope "<change-description>"
```

Screenshots must be temporary:

```text
tmp/ui-checks/
```

They must not enter the final package, must not remain as permanent artifacts, and must be deleted after evaluation unless the user explicitly asks to keep them.

Important rules:

- screenshots must cover the pages, modals, menus, states, and flows touched by the current change;
- if a shared component changes, inspect at least two real contexts where it appears;
- if an interactive state changes, inspect the open/active state too;
- after using screenshots to check layout, overlaps, readability, scrolling, and visual coherence, delete them.

### Visual Design Critique And Scoring

A screenshot check is not complete when the interface merely avoids clipping,
overlap or broken controls. Those are minimum correctness conditions, not evidence of
good design. After every visual change, review the screenshots as a critical app
designer who did not write the implementation. Actively look for decisions that feel
accidental, unbalanced, noisy, inconsistent or merely tolerated because the code is
already finished.

Score every touched visual surface or state separately from 1 to 10, for example the
closed toolbar, open menu, modal, table and responsive variant. Evaluate at least:

- information hierarchy and discoverability;
- composition and use of space;
- alignment, rhythm and spacing;
- typography, control dimensions and visual proportions;
- consistency with the rest of the app and the `mystudio-platform` language;
- responsive behavior and the relationship between closed, open, loading, success
  and error states.

Also assign an overall score, but do not let a strong modal hide a weak base layout.
State the concrete reasons behind each score. Use this interpretation:

- `9-10`: polished and ready to establish a design standard;
- `7-8`: professionally acceptable, with only minor refinements;
- `5-6`: functional but visibly unresolved;
- below `5`: should not be considered complete.

If any touched state scores below `7`, do not close the UI point merely because tests
pass. Refine it and inspect it again, or explicitly document the unresolved tradeoff
and obtain user acceptance. Avoid confirmation bias: ask whether a professional app
designer would approve the result if it had been produced by someone else.

Record the scores, criticisms and any resulting iteration in the notebook for
structural UI work. In the final response, report the visual judgment separately from
functional verification. Never describe a screenshot review as successful solely
because no technical rendering defect was found.

Preferred data order for UI checks:

1. controlled test runtime or sample data;
2. real app for navigation and screenshots only;
3. real app with write actions only when explicitly requested.

Do not use Playwright to confirm destructive actions on real data without explicit authorization.

When Playwright is used, report:

- command run;
- views checked;
- temporary screenshots produced and deleted;
- problems found;
- limits of the check.

If Playwright is unavailable, say so explicitly.

## Code Modification Rules

- Use `rg` for searching.
- Use `apply_patch` for manual edits.
- Bulk documentation rewrites may use a direct file rewrite when patching is less reliable than replacing the document.
- Do not delete or revert user changes unless explicitly requested.
- Do not perform lateral refactors unless needed for the point.
- Before editing important files, briefly explain what will change.
- Verify with commands proportional to risk.
