---
cypilot: true
type: spec
name: Kit Specification
version: 1.0
purpose: Define kit structure, installation, update model, directory layout, generated output overview, taxonomy, and extension protocol
drivers:
  - cpt-cypilot-fr-core-kits
  - cpt-cypilot-fr-sdlc-plugin
---

# Kit Specification


<!-- toc -->

- [Kit Overview](#kit-overview)
- [Kit Directory Structure](#kit-directory-structure)
  - [User Kit (in {cypilot_path}/kits/)](#user-kit-in-cypilotpathkits)
  - [Generated Outputs (in config/kits/)](#generated-outputs-in-configkits)
- [Generated Outputs](#generated-outputs)
- [Project-Level Outputs](#project-level-outputs)
  - [taxonomy.md](#taxonomymd)
- [Kit Extension Protocol (p2)](#kit-extension-protocol-p2)
  - [Registering Custom Markers](#registering-custom-markers)
  - [Output Generator Interface](#output-generator-interface)
- [Related Specifications](#related-specifications)

<!-- /toc -->

---
---

## Kit Overview

A **Kit** is a blueprint package that provides domain-specific artifact and codebase definitions for Cypilot. Each kit contains one or more **Blueprints** — single-source-of-truth Markdown files from which all kit resources are deterministically generated.

Blueprints with an `artifact` key in `@cpt:blueprint` define artifact kinds (e.g., PRD, DESIGN). Blueprints without an `artifact` key define codebase-level resources (rules, checklists for code). See [Blueprint Specification](blueprint.md) for the full blueprint format, markers, parsing algorithm, and update model.

**What a kit provides**:
- Blueprint files (`blueprints/*.md`) — one per artifact kind or codebase concern (required)

**What is generated from blueprints** (placed in `{cypilot_path}/config/kits/<slug>/`):
- Per-artifact outputs: `artifacts/<KIND>/` containing `template.md`, `rules.md`, `checklist.md`, `example.md`
- Codebase outputs: `codebase/` containing `rules.md`, `checklist.md`
- Kit-wide: `constraints.toml` (aggregated from all artifact blueprints)
- Workflow files from `@cpt:workflow` markers: `workflows/{name}.md`
- SKILL.md extensions from `@cpt:skill` markers

**Key properties**:
- Kit registration (slug, version) is stored in `{cypilot_path}/config/core.toml`
- Blueprints are the single source — all other resources are generated
- Deterministic: same blueprint → identical output files (byte-for-byte)
- User-customizable: blueprints are copied into `{cypilot_path}/kits/<slug>/blueprints/` where users can edit them and regenerate outputs
- Update model protects user modifications across kit updates (see [blueprint.md § Update Model](blueprint.md#update-model))

> **Plugin system** (CLI subcommands, validation hooks, generation hooks) is planned for p2 and not covered in this specification.

---

## Kit Directory Structure

### User Kit (in {cypilot_path}/kits/)

When a kit is installed, blueprints are copied to `{cypilot_path}/kits/{slug}/` where users can edit them:

```
{cypilot_path}/kits/<slug>/
├── blueprints/                    # User-editable blueprints (one .md per artifact kind)
│   ├── PRD.md
│   ├── DESIGN.md
│   ├── ADR.md
│   ├── CODEBASE.md                # No artifact key → codebase outputs
│   └── ...
└── conf.toml                      # Kit version metadata
```

- `blueprints/` is the **minimum required structure**. For blueprints with `artifact` key, the filename (without `.md`) becomes the artifact kind slug (e.g., `PRD.md` → artifact kind `PRD`). Blueprints without `artifact` key generate into `codebase/`.
- Customization detection: the kit **source** (cache) contains `blueprint_hashes.toml` with SHA-256 hashes keyed by version. At update time, user’s blueprint hash is computed on the fly and compared against the stored hash for the user’s installed version. Matching → not customized → auto-update; different → customized → interactive diff. The hash file is **never** copied into user projects.

### Generated Outputs (in config/kits/)

All outputs are generated from user blueprints into `config/kits/{slug}/`:

```
config/kits/<slug>/
├── SKILL.md                       # Generated: per-kit skill instructions
├── constraints.toml               # Generated: kit-wide structural constraints (from all artifact blueprints)
├── artifacts/                     # Generated outputs per artifact kind
│   ├── PRD/
│   │   ├── template.md            # Generated: heading structure
│   │   ├── rules.md               # Generated: agent rules
│   │   ├── checklist.md           # Generated: quality checklist
│   │   └── examples/example.md    # Generated: concrete example
│   ├── DESIGN/
│   │   └── ...
│   └── .../
├── codebase/                      # Generated from blueprints without artifact key
│   ├── rules.md               # Generated: codebase agent rules
│   └── checklist.md           # Generated: codebase quality checklist
├── scripts/                       # Copied from kit source
│   └── ...
└── workflows/                     # Generated from @cpt:workflow markers
    ├── pr-review.md
    ├── pr-status.md
    └── ...
```

Top-level `.gen/` retains only aggregate files: `AGENTS.md`, `SKILL.md`, `README.md`.

**Flow**:
1. `cpt init` / `cypilot kit install` copies blueprints to `{cypilot_path}/kits/{slug}/blueprints/` (user-editable)
2. Blueprint Processor reads user blueprints and generates outputs into `config/kits/{slug}/` (`artifacts/<KIND>/`, `codebase/`, `constraints.toml`, `workflows/`)
3. Users edit blueprints in `{cypilot_path}/kits/{slug}/blueprints/` and run `cpt generate-resources` to regenerate outputs

**Update modes** (see also [blueprint.md § Update Model](blueprint.md#update-model)):

| Mode | Command | Behavior |
|------|---------|----------|
| **Force** | `cypilot kit update --force` | Overwrites all user blueprints in `{cypilot_path}/kits/{slug}/`, regenerates all outputs. User edits are discarded. |
| **Smart** (default) | `cypilot kit update` | Hash-based customization detection: compute SHA-256 of each user blueprint, compare against known hashes for user’s installed version from source `blueprint_hashes.toml`. Unmodified blueprints are auto-updated silently; customized blueprints are presented via interactive diff. Regenerated `config/kits/` outputs are reviewed via the Resource Diff Engine (interactive file-level accept/reject/modify). See [blueprint.md § Smart Update](blueprint.md#smart-update-default). |

---

## Generated Outputs

All outputs are generated by the core Blueprint Processor from `@cpt:` markers. Kits do not define custom output generators in p1 — all generation logic is in the core.

| Output | Location | Source Markers | Spec |
|--------|----------|---------------|------|
| `rules.md` | `artifacts/<KIND>/` | `@cpt:rules` + `@cpt:rule` | [rules.md](rules.md) |
| `checklist.md` | `artifacts/<KIND>/` | `@cpt:checklist` + `@cpt:check` | [checklist.md](checklist.md) |
| `template.md` | `artifacts/<KIND>/` | `@cpt:heading` + `@cpt:prompt` | [template.md](template.md) |
| `example.md` | `artifacts/<KIND>/` | `@cpt:heading` (examples) + `@cpt:example` | [example.md](example.md) |
| `constraints.toml` | kit root | `@cpt:heading` + `@cpt:id` (aggregated) | [constraints.md](constraints.md) |
| codebase `rules.md` | `codebase/` | `@cpt:rules` + `@cpt:rule` | [rules.md](rules.md) |
| codebase `checklist.md` | `codebase/` | `@cpt:checklist` + `@cpt:check` | [checklist.md](checklist.md) |
| `workflows/{name}.md` | `workflows/` | `@cpt:workflow` | [blueprint.md § cpt:workflow](blueprint.md#cptworkflow) |

**Determinism guarantee**: same blueprint content → identical output files (byte-for-byte). The processor sorts, formats, and serializes deterministically.

---

## Project-Level Outputs

### taxonomy.md

`taxonomy.md` is a **generated** kit-level document. It aggregates information from the kit's blueprints into a single human-readable reference.

**Location**: `{cypilot_path}/config/kits/{slug}/taxonomy.md`

**Source data** (collected from the kit's blueprints):
- `@cpt:blueprint` — artifact kind name, description, kit identity
- `@cpt:id` — identifier kind names, descriptions, examples, `to_code` flags
- `@cpt:heading` — section descriptions (from `description` keys)
- `@cpt:example` — concrete example snippets per artifact kind
- `@cpt:blueprint` `intro` field — general taxonomy introduction text

**Structure of generated taxonomy.md**:

```markdown
# Project Taxonomy

## Introduction

{Aggregated intro text from @cpt:blueprint `intro` fields across all kits.
Provides project-level context: what artifacts exist, how they relate,
and how identifiers connect design to implementation.}

## Artifact Kinds

### PRD — Product Requirements Document

{Description from @cpt:blueprint of PRD}

**Identifier kinds**:
| Kind | Name | Description | To Code |
|------|------|-------------|---------|
| `fr` | Functional Requirement | ... | yes |
| `nfr` | Non-functional Requirement | ... | no |
| `actor` | Actor | ... | no |

**Example IDs**: `cpt-myapp-fr-login`, `cpt-myapp-nfr-latency`

### DESIGN — Technical Design

{Description from @cpt:blueprint of DESIGN}

...

## Cross-Artifact Traceability

{Summary of how IDs flow between artifact kinds,
derived from @cpt:id ref.* rules across all blueprints.}
```

**Blueprint `intro` field**: each blueprint's `@cpt:blueprint` TOML block MAY include an `intro` key with general taxonomy text for the introduction section:

```toml
# Inside @cpt:blueprint
intro = "PRD defines the product requirements and serves as the primary input for DESIGN."
```

**Generation command**: `cpt generate-resources`

---

## Kit Extension Protocol (p2)

> **p2**: The plugin system (custom markers, output generators) is planned for a future phase. The following documents the target design for reference.

### Registering Custom Markers

Kits register markers during plugin initialization:

```python
def register_blueprint_markers(processor):
    processor.register_marker(
        name="heading",
        content_mode="template-visible",
        handler=HeadingMarkerHandler(),
        generators=[ConstraintsTomlGenerator()]
    )
    processor.register_marker(
        name="check",
        content_mode="metadata",
        handler=CheckMarkerHandler(),
        generators=[ChecklistMdGenerator()]
    )
```

Each registration provides:
- **name** — marker name (used as `cpt:{name}`)
- **content_mode** — `template-visible` (content kept in template.md) or `metadata` (content stripped)
- **handler** — parser that extracts structured data from the marker
- **generators** — list of output generators that consume this marker's data

### Output Generator Interface

Each generator implements:

```python
class OutputGenerator:
    def output_filename(self) -> str:
        """Return the output file name (e.g., 'checklist.md')"""

    def generate(self, markers: list[MarkerData], context: BlueprintContext) -> str:
        """Generate file content from collected markers"""
```

The Blueprint Processor:
1. Parses all markers, grouping by type.
2. For each registered generator, collects relevant markers.
3. Invokes `generate()` with the collected markers.
4. Writes the output to `output_filename()` in the artifact directory.

---

## Related Specifications

| Spec | Description |
|------|-------------|
| [blueprint.md](blueprint.md) | Blueprint format, marker syntax, marker reference, placeholder syntax, parsing algorithm, update model, validation rules |
| [rules.md](rules.md) | Generated `rules.md` format, structure, loading, and usage |
| [checklist.md](checklist.md) | Generated `checklist.md` format, domain organization, check items |
| [template.md](template.md) | Generated `template.md` format, heading structure, placeholders |
| [constraints.md](constraints.md) | Generated `constraints.toml` format, validation semantics, cross-artifact rules |
| [example.md](example.md) | Generated `example.md` format, derivation from blueprint examples |
