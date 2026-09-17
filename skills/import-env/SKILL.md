---
name: import-env
description: >
  Guides importing existing (brownfield) infrastructure into Torque. ALWAYS start by surfacing
  the goal of the import: manage one live environment in place (cost visibility, scheduling,
  day-2 updates, workflows, drift detection) versus build a digital twin that can also deploy
  copies. The two produce near-identical files but are governed by opposite assumptions.
  Use when the user says "import my existing cluster into Torque", "brownfield import",
  "bring running infrastructure under Torque management", "import an environment",
  "terraformer output to a blueprint", "track cost of an existing environment",
  "can I relaunch an imported blueprint", "will launching this modify production",
  "digital twin of production", "templatize an existing environment", or invokes /import-env.
  Also use when reviewing an import-generated Terraform module, or a blueprint that wraps one.
argument-hint: "[resource-or-env]"
---

Import existing infrastructure into Torque without breaking it.

An import produces a blueprint and an IaC asset that look exactly like every other blueprint
and asset in the repo. They are not the same thing, and the difference is decided by the
user's goal — not by the files. Surface the goal first.

---

## Step 1 — Surface the goal (blocking; do this before writing anything)

Do **not** scaffold, parameterize, or write any YAML until the user has answered this. Ask:

> What do you want this import to do for you?
>
> **(A) Manage this one running environment** — see what it costs, schedule it down when
> idle, run day-2 workflows against it, change its configuration, detect drift.
>
> **(B) Use it as a source for new environments** — a digital twin of production, or
> turning a long-running environment into something launchable on demand.

Route by the answer:

| The user says | Track |
|---|---|
| "see what it costs", "schedule it off at night", "stop paying for idle" | **A** |
| "change its node count / size / version from Torque", "run a workflow on it" | **A** |
| "detect drift", "know when someone changes it by hand" | **A** |
| "get visibility", "bring it under management", vague / unsure | **A** (default — say so) |
| "spin up a dev copy of prod", "digital twin", "clone this environment" | **B** |
| "make this launchable on demand", "templatize this app", "ephemeral copies" | **B** |

**Track A is the overwhelming default — roughly 99 of 100 imports.** If the user is unsure,
choose A, say you are choosing it, and state the consequence: *the resulting blueprint
represents one specific live environment and is not intended to deploy a second one.*

If the answer is "both", that is Track B — and Track B has to be committed to **before** the
import, because it changes what you import. See Step 4.

---

## Step 2 — Track A: the blueprint represents one live environment

### The rule

> A blueprint produced by a brownfield import represents one specific live environment.
> It is not intended to deploy a second one.

Making it dynamic is still worth doing — but **not so the user can launch copies**. You
parameterize it so the user can *change inputs to update the infrastructure that already
exists*. That is the entire purpose of its inputs. Say this out loud to the user; it is the
single correction that dissolves most brownfield confusion.

### Why it cannot double as a template

This is not a platform restriction. It falls out of a decision the user already made
correctly when choosing what to import.

Most imports exist to get visibility and control over something already running. Cost is by
far the most common driver, then day-two modification, workflows, and drift detection. None
of that requires codifying the network the workload sits in, the IAM bindings it
authenticates with, or the security rules around it. Those already exist and already work —
so you import the handful of high-value, cost-incurring resources you actually want to
manage, and leave the rest alone.

A blueprint that genuinely *creates* an environment has no such luxury: nothing exists yet,
so every supporting piece has to be in the configuration.

> **The real reason:** an import blueprint cannot clone its environment because the user
> never codified the things a clone would need — and for what they were doing, they were
> right not to.

### Two models, easily mistaken for one

| | Greenfield — blueprint as template | Brownfield — blueprint as a live environment |
|---|---|---|
| **Purpose** | Deploy many independent environments from one definition | Bring one existing, running environment under management |
| **Relaunching** | Expected; that is the point | Not supported — fails on conflicts, harmlessly |
| **Inputs exist to** | Configure each new copy independently | Update infrastructure that already exists |
| **State** | Fresh, isolated state file per environment | Bound to the one state file describing the live resources |
| **Resource naming** | Parameterized or suffixed to avoid collisions | Fixed — the names already exist in the cloud |
| **Scope** | Everything the workload needs: networking, security, IAM | Just the resources worth visibility and control; supporting infra correctly left out |

### If someone launches a second environment anyway

Users ask this with real urgency — *"the state already exists, won't Terraform apply changes
to production?"* Walk them through the mechanism. Nothing here is enforced by Torque; it
falls out of how Terraform and the cloud provider already behave.

1. Someone launches a second environment from the import blueprint.
2. Torque computes a **fresh state key** for that environment. It points at an empty state file.
3. Terraform reads that empty state, sees no resources recorded, and plans to **create** everything.
4. It asks the provider to create a resource using the name already baked into the configuration.
5. The provider rejects it: **that name is already taken.** The launch fails. The live environment is never touched.

> **Why this is safe, not lucky:** Terraform only destroys what is recorded in its state. An
> empty state has nothing to destroy, so it can never produce a destroy plan for resources it
> did not create.

The only way to make Terraform destroy a pre-existing resource is to deliberately import that
resource into a state file and then run a destroy against it. An accidental second launch
fails at the create step, well before anything is at risk.

---

## Step 3 — Track A authoring rules

### Keep the `backend` block out of the Terraform asset

Terraform's native `backend` block cannot reference variables, inputs, or any expression —
every value must be a literal fixed at authoring time. Define the backend in the **blueprint**
instead (`spec.backend`, see `author-blueprint`), where Torque supplies it dynamically. Worth
doing in essentially every case, brownfield or not, purely because the native block is so rigid.

### Name the blueprint and module for what they are

`Imported GKE Cluster` / `imported-gke-cluster` carries the constraint to anyone browsing a
catalog. A generic name invites exactly the second launch the asset cannot support.

### Store it where the name signals "not reusable"

Keep the canonical layout from `repo-conventions` (`terraform/<module>/`, assets grouped by
kind) and carry the signal in the folder name: `terraform/imported-gke-cluster/`. A reader
should not have to open the files to learn the module is not reusable. A team that prefers
hard separation can use a dedicated `imported/` tree instead — say which you chose and why.

### Be honest about which inputs actually do something

Any field pinned by `lifecycle.ignore_changes`, or that the provider only reads at creation
time, cannot affect an already-imported resource. Exposing it as an input is misleading unless
its description says plainly that it is inert here. Prefer inputs that genuinely can update
live infrastructure.

---

## Step 4 — Track B: digital twin / templatizing

A single blueprint *can* be both the source of an imported environment and a template for
copies. That is legitimate. It is rare — call it one import in a hundred — and it is never
discovered after the fact.

Two independent bars have to be cleared. Most imports clear neither. Check both explicitly
with the user before importing:

**Bar one — completeness.** Everything the workload depends on has to be captured:
networking, security, IAM, address ranges, the lot — not just the high-value resources an
ordinary import focuses on. If a piece is missing, a copy built from the configuration will
not stand up.

**Bar two — parameterization.** Every name, every range, and every value that must be unique
per instance has to become an input, so two instances can coexist without colliding. Rare on
its own, because import-generated configuration is written entirely in literals.

Legitimate reasons to take this on:

- **A digital twin.** Production exists, dev or staging does not. Import the real thing, parameterize it, stand up a right-sized clone from the same definition.
- **Templatizing an application.** Turn a long-running environment into something launched and torn down on demand.

If the user is asking about Track B **after** an import already happened, tell them plainly:
retrofitting an existing import into a template is usually more work than building the
template from scratch. Offer both paths and let them choose.

For Track B, hand the module to `reusable-terraform` for the parameterization pass, and run
`blueprint-review` / `/deploy-check` on the result.

---

## Step 5 — Traps in import-generated configuration

Applies to both tracks. Observed bringing a live GKE cluster under management; the shapes
recur across providers.

### Not every same-named field means the same thing — *can destroy the resource*

A cluster resource and a node pool resource can both expose a field called
`initial_node_count`. On the node pool it is an ordinary creation-time count. On the cluster
it is a legacy bootstrap field for an implicit default pool — and it **forces replacement**.

Wiring one variable to both planned a full destroy and recreate of a running cluster, taking
every attached node pool with it, including one not in the configuration at all. **Check
whether a field forces replacement before parameterizing it** — read the provider docs for
the specific resource, not the field name.

### Let outside owners own their fields — *perpetual diff*

When something outside Terraform legitimately changes a value — a managed auto-upgrade moving
a version forward, a scaling workflow changing a node count — Terraform proposes reverting it
on every plan, forever. Name those fields in `lifecycle.ignore_changes`. Same pattern as
autoscaling group capacity and auto-upgraded database engine versions; it is the price of
letting another system own a field.

### Import-generated configuration has no relationships — *silent drift*

Import output writes literal values everywhere. A cluster references its network as a
hardcoded string rather than pointing at the network resource beside it, so Terraform knows
of no dependency. For infrastructure the module should not own, convert those references to
**data sources**. The relationship becomes real, and a rename surfaces at plan time instead
of silently pointing at something that no longer resolves.

### Moving the backend changes local Terraform runs — *workflow*

Once the backend lives in the blueprint, the module declares none. A bare `terraform init` in
that directory offers to migrate state *out* of remote storage onto local disk. **Decline it.**
Pass the backend explicitly with `-backend-config` flags for local runs, and expect
credentials to differ too: a module authenticating through a workload identity file that only
exists inside the execution agent cannot resolve it from a laptop.

---

## Step 6 — Verify, and flag the open question

The plugin does not wrap a Torque import endpoint. Perform the import itself in the Torque
portal, then inspect the result with
`python3 ${CLAUDE_PLUGIN_ROOT}/skills/zero-touch-api/scripts/examples/get_environment.py`
(status moves `Importing` → `Active`). See `zero-touch-api` before adding any new call.

**Unverified — surface this to the user rather than asserting either way.** When the backend
lives in the blueprint, Torque derives a state key that includes the environment and grain
identifiers. An import API call, by contrast, can supply an exact literal key binding the
environment to its existing state file. Which of the two governs **day-two updates** after the
import completes is not confirmed. If updates re-derive the key from the blueprint rather than
reusing the one supplied at import, an update would meet an empty state and fail on name
conflicts. Recommend testing it deliberately — release, re-import, change an input, update —
before relying on day-two updates in anger.

---

## Never do

- **Never** scaffold an import before the user has answered the Step 1 goal question.
- **Never** present an import blueprint as reusable, or suggest launching a second environment from one.
- **Never** parameterize a field without checking whether it forces replacement.
- **Never** silently retrofit an import into a template — say what it costs and let the user choose.
- **Never** leave a `backend` block in an imported Terraform module; move it to the blueprint.
- **Never** expose an input that cannot affect the live resource without saying so in its description.
