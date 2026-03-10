# SemaBridge Technical Feature Documentation for React Porting

## Purpose

This document is a **technical feature map** of the current SemaBridge codebase, designed specifically for porting the existing desktop/CLI product into a React application with the following screens:

1. Explore Screen
2. Projects Screen
3. Project Jobs Screen
4. Model-to-Model Connection Screen
5. Settings Screen

It covers:
- What is implemented today (from code)
- What data already exists in persistence
- What backend capabilities should be exposed to React
- What is missing and should be added for parity with your requested UI

---

## 1) Current Product Architecture (What React will sit on top of)

SemaBridge currently has a **pipeline-first architecture** that is invoked via CLI and a Qt desktop UI:

- **Execution pipeline**: fixed 10-step flow (config → IDs → auth → extract → validate → convert → persist → target-convert → deploy → finalize).
- **Connector layer**: Snowflake + Fabric connectors with discovery/extract/deploy capabilities.
- **Converter layer**: transformations across TMSL, OSI, SML, SQL + DAX safety/override pipeline.
- **Repository layer**: DuckDB-based versioning/audit tables (projects, snapshots, changes, runs, source artifacts).
- **Orchestration options**: local execution engine + Temporal durable workflows.
- **User interfaces today**: CLI and Qt widgets (source browser, history, diff/rollback patterns).

### Key implication for React
The React app should be implemented as a **new presentation layer + API layer** over existing pipeline/orchestration/repository services, not as a rewrite of core semantics logic.

---

## 2) Core Technical Features Inventory

## A. Pipeline and run lifecycle

### What exists now
- Mandatory 10-step execution model in `ExecutionEngine`.
- Strong run identity model (`project_id`, `run_id`).
- Optional target conversion/deployment phases.
- Run summaries and step-level status tracking.

### Why this matters for React
- React should represent each run as a first-class entity.
- The Project Jobs and History UI should surface step-level execution details (not just success/fail).
- Any scheduled job should ultimately enqueue or trigger this same pipeline.

## B. Connectors and metadata discovery

### What exists now
- Snowflake and Fabric are both source/target capable.
- Discovery hooks are already used by UI worker threads:
  - Fabric: list semantic models
  - Snowflake: list semantic views
  - Repository: list tracked projects
- Inference modules exist for relationship detection, hierarchy detection, measure detection, and table-type inference.

### Why this matters for React
- The Explore and Projects screens can reuse existing discovery logic.
- Auto-generated mapping/relationship suggestions can leverage current inference modules.

## C. Canonical model transformation

### What exists now
- Multi-format conversion pipeline around OSI/SML.
- DAX translation with tiering and override generation for advanced measures.
- Intermediate canonical model representation designed for cross-platform sync.

### Why this matters for React
- Model-to-Model mapping can use canonical representations to normalize both sides before matching.
- “Auto-map then allow human override” aligns with current converter philosophy.

## D. Persistence and versioning

### What exists now
- DuckDB repository schema includes:
  - `projects`
  - `snapshots`
  - `changes`
  - `runs`
  - `source_artifacts`
- Snapshot diffing/compare/rollback APIs exist in repository manager.
- Additional SQLAlchemy ORM models exist for `snapshots`, `versions`, and `sync_runs` (DuckDB/Postgres configurable via `DATABASE_URL`).

### Why this matters for React
- Core run history and model versioning are already present.
- Some of your requested UI entities (jobs, connection profiles, explicit mapping overrides) need new tables.

## E. Current UI capabilities (Qt)

### What exists now
- Source selector + model discovery + multi-select + filter + select all/clear.
- Version history timeline with compare and rollback.
- Initialization wizard for repository/log setup.
- Theme and desktop UX infrastructure.

### Why this matters for React
- There is existing UX logic to translate into web flows (especially discovery and history).
- The React migration can preserve behavior while improving ergonomics and scale.

---

## 3) React Screen-by-Screen Feature Mapping

## Screen 1: Explore Screen (Semantic models in map view)

### Business goal
Visual exploration of semantic models and relationships in a graph/map-like experience (Power BI style).

### Existing backend capabilities to reuse
- Connector discovery (`list_semantic_models`, `list_semantic_views`) to populate model nodes.
- Relationship/hierarchy inference modules to derive edges and semantic groupings.
- Snapshot artifacts for loading historical structure.

### Proposed React feature set
- Graph canvas (nodes: models/tables/metrics; edges: relationships/dependencies).
- Filters:
  - Source system
  - Workspace/project
  - Last updated/run status
  - Search by model/metric/table name
- Inspector panel showing:
  - Model metadata
  - Relationship details
  - Recent run IDs touching that model

### API endpoints to introduce
- `GET /explore/models?source=fabric|snowflake|repository`
- `GET /explore/model/{model_id}/graph`
- `GET /explore/model/{model_id}/lineage`

### Data model notes
No hard blocker in current schema; graph responses can be built from source discovery + inferred relationships + stored snapshots.

---

## Screen 2: Projects Screen (Select sources/models, save to DB, group/classify)

### Business goal
Create and manage project definitions containing selected source + model scope, with organization (group/classification).

### Existing backend capabilities to reuse
- Existing `projects` table already stores project identity and basic metadata.
- Existing discovery UI pattern can drive source/model selection.

### Gaps to implement
Current schema does **not** persist:
- many-to-many project-to-model selections
- project grouping/classification taxonomy

### Recommended DB extensions
- `project_models`
  - `project_id`, `source_type`, `model_id`, `model_name`, `selected_at`
- `project_groups`
  - `group_id`, `name`, `description`, `color`
- `project_group_membership`
  - `project_id`, `group_id`
- Optional classification fields on `projects`
  - `classification`, `owner`, `tags_json`, `active`

### Proposed React feature set
- Create/edit/archive project
- Select source connector and discover models
- Multi-select models and persist
- Assign group/category/tags
- Bulk operations (assign group, activate/deactivate)

### API endpoints to introduce
- `POST /projects`
- `GET /projects`
- `GET /projects/{project_id}`
- `PUT /projects/{project_id}`
- `POST /projects/{project_id}/models`
- `GET /projects/{project_id}/models`
- `POST /project-groups`
- `POST /project-groups/{group_id}/projects/{project_id}`

---

## Screen 3: Project Jobs Screen (automation config + run history + copy jobs)

### Business goal
Configure recurring jobs per project, show run history with Run IDs, and enable copy/clone job configurations.

### Existing backend capabilities to reuse
- Run ID and run lifecycle are already central (`run_id` generated and persisted).
- `runs` table captures execution-level status, timing, source/target, errors.
- Temporal workflows support durable orchestration semantics.

### Gaps to implement
Current code tracks **runs**, but does not define persistent **job configuration objects** with scheduler metadata.

### Recommended DB extensions
- `project_jobs`
  - `job_id`, `project_id`, `name`, `description`, `schedule_type`, `cron_expr`, `timezone`, `enabled`, `source_type`, `target_type`, `deploy`, `dry_run`, `config_json`, `created_at`, `updated_at`
- `job_runs`
  - `job_run_id`, `job_id`, `run_id`, `trigger_type` (manual/schedule/api), `triggered_by`, `started_at`, `completed_at`, `status`, `error_message`
- `job_run_steps` (optional)
  - step-level statuses for UI timelines

### Copy job configuration requirement
Implement clone endpoint:
- `POST /project-jobs/{job_id}/clone`
  - duplicates job config with new name + metadata

### Proposed React feature set
- Job list per project (enabled/schedule/last run/next run)
- Job editor wizard (simple mode + advanced JSON mode)
- Clone action (“Copy as new job”)
- Run history table (Run ID deep links)
- Per-run details drawer with step statuses and logs

### API endpoints to introduce
- `POST /projects/{project_id}/jobs`
- `GET /projects/{project_id}/jobs`
- `PUT /project-jobs/{job_id}`
- `POST /project-jobs/{job_id}/run`
- `POST /project-jobs/{job_id}/clone`
- `GET /project-jobs/{job_id}/runs`
- `GET /runs/{run_id}`

---

## Screen 4: Model-to-Model Connection Screen

### Business goal
Provide an interactive mapping interface for model names, columns, and metrics between source and target models, with automated suggestions + user overrides.

### Existing backend capabilities to reuse
- Semantic diff and comparison primitives.
- Inference engine and detectors for semantic hints.
- Canonical OSI/SML conversion pipeline that can normalize source/target structures.
- Override-oriented design already exists in measure safety pipeline (conceptually aligned with user overrides).

### Gaps to implement
No dedicated persistent mapping model currently exists for:
- model-level mapping pairs
- field/metric mapping rules
- confidence scores
- manual override tracking

### Recommended DB extensions
- `model_mappings`
  - `mapping_id`, `project_id`, `source_model_id`, `target_model_id`, `status`, `auto_generated_at`, `approved_at`, `approved_by`
- `field_mappings`
  - `field_mapping_id`, `mapping_id`, `mapping_type` (column/metric/model-name), `source_path`, `target_path`, `confidence`, `source_rule`, `is_override`, `override_reason`, `updated_by`, `updated_at`
- `mapping_versions` (optional)
  - immutable snapshots for audit/rollback of mapping edits

### Auto mapping engine approach
1. Normalize both models to canonical graph (OSI/SML-backed DTO).
2. Generate candidates via exact match + normalized-name match + datatype compatibility + relationship context.
3. Assign confidence scores.
4. Persist candidate set as auto-generated baseline.
5. Mark user edits as explicit overrides.

### React interaction design
- Dual-pane source/target trees.
- Center mapping canvas with drag-and-drop linking.
- Confidence badges + conflict warnings.
- “Accept all high confidence” action.
- Audit trail of overrides.

### API endpoints to introduce
- `POST /projects/{project_id}/mappings/auto-generate`
- `GET /projects/{project_id}/mappings/{mapping_id}`
- `PUT /projects/{project_id}/mappings/{mapping_id}/field-mappings`
- `POST /projects/{project_id}/mappings/{mapping_id}/approve`

---

## Screen 5: Settings Screen (connectors/systems + OAuth OR env entry)

### Business goal
Manage named connection profiles for multiple systems (generic connector, Snowflake, Databricks, etc.) with secure configuration.

### Existing backend capabilities to reuse
- Strong typed settings exist for Snowflake and Fabric.
- Fabric auth path is already OAuth client credentials style (tenant/client/secret).
- Environment-variable based loading is implemented and validated.

### Gaps to implement
Current settings are mostly process/global and env-driven, not multi-connection profile records editable in UI.

### Recommended DB extensions
- `connections`
  - `connection_id`, `name`, `connector_type`, `auth_mode` (`oauth` or `env`), `config_encrypted`, `created_at`, `updated_at`, `is_active`
- `connection_bindings`
  - bind projects/jobs to a named connection

### Security requirements
- Encrypt secrets at rest (`config_encrypted`).
- Never return raw secrets in API responses.
- Test connection endpoint should use ephemeral decrypted credentials in memory only.

### Proposed React feature set
- Connection list and create/edit/delete
- Connector type selector:
  - generic
  - Snowflake
  - Databricks
  - Fabric/Power BI
- Auth mode selector (OAuth OR env-entry form)
- “Test connection” action
- Name/description/tagging for each saved connection

### API endpoints to introduce
- `POST /connections`
- `GET /connections`
- `PUT /connections/{connection_id}`
- `DELETE /connections/{connection_id}`
- `POST /connections/{connection_id}/test`

---

## 4) Suggested Domain Model for React + API

Create explicit backend domains to keep React pages clean:

- **ExploreDomainService**: graph and lineage views
- **ProjectDomainService**: project metadata + model selections + grouping
- **JobDomainService**: schedules, triggers, history, clone
- **MappingDomainService**: auto-map generation + user overrides + approval
- **ConnectionDomainService**: connector profiles + credential strategy

This keeps React components thin and shifts business logic to typed service boundaries.

---

## 5) Recommended Rollout Plan

### Phase 1 (fastest value)
- Build Projects + Settings + Project Jobs CRUD APIs.
- Reuse existing run execution engine for manual triggers.
- Implement React pages for Settings, Projects, basic Jobs + Run History.

### Phase 2
- Add Explore graph endpoints and model map UI.
- Add richer run detail, logs, and step visualizations.

### Phase 3
- Add Model-to-Model mapping engine + drag-drop editor + override persistence.
- Add approval/versioning workflows for mappings.

### Phase 4
- Integrate Temporal scheduling for production-grade job orchestration.
- Add RBAC, audit controls, and tenant isolation if needed.

---

## 6) Implementation Notes for Porting Team

- Keep all model conversion/inference logic in Python backend services.
- Avoid moving semantic inference into React.
- React should consume stable DTOs:
  - `ProjectDTO`, `JobDTO`, `RunDTO`, `ModelNodeDTO`, `MappingDTO`, `ConnectionDTO`.
- Preserve run_id as the universal trace ID across logs, runs, jobs, and UI links.
- Build with optimistic UI updates only for low-risk edits (labels/tags), but use pessimistic save for mappings/job configs.

---

## 7) Feature Matrix (At-a-Glance)

| Requested Screen Capability | Current Status | Reuse from Existing Code | New Work Required |
|---|---|---|---|
| Explore map view | Partial | discovery + inference + snapshots | graph API + React canvas |
| Projects with saved selections | Partial | projects table + discovery | project-model association tables + grouping/classification |
| Jobs config + history + copy | Partial | run tracking + orchestration | job config schema + clone + scheduler metadata |
| Model-to-model auto mapping + user override + drag/drop | Partial | canonical conversion + inference patterns | mapping persistence + auto-match service + DnD UI |
| Settings with named connectors + OAuth/env | Partial | typed settings validation + connector auth flows | multi-profile connection storage + secret management + settings APIs |

---

## 8) Final Recommendation

For your React migration, treat SemaBridge as a **semantic orchestration backend** and add a dedicated HTTP API facade. The core technical foundation is already strong (pipeline, connectors, conversion, versioning, runs), but your target product requires explicit persistence models for jobs, mappings, and connection profiles. Implement those as first-class entities, then map each React screen directly to a backend domain service.
