# Vast Inventory And Readiness UX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `vasted up` accurately find real Vast inventory, explain when inventory exists but filters eliminate it, and surface clearer startup/readiness progress.

**Architecture:** Tighten Vast search semantics in `app.vast` with explicit GPU aliases and correct VRAM units, then thread richer preflight/readiness data through `app.service` and `app.commands.up`. Add focused tests around query construction and timeout/progress formatting so the launch path remains deterministic.

**Tech Stack:** Python 3.12, Click, Rich, HTTPX, pytest

---

### Task 1: Fix Vast GPU search semantics

**Files:**
- Modify: `app/config.py`
- Modify: `app/vast.py`
- Test: `tests/test_vast.py`

**Step 1: Write the failing tests**

Add tests that assert:
- `1xa100-80gb` builds a `/bundles/` query using `gpu_name: {"in": ["A100 SXM4", "A100 PCIe"]}`.
- `1xa100-80gb` uses `gpu_ram: {"gte": 81920}`.
- `1xh100` includes known H100 aliases and preserves `num_gpus`.

**Step 2: Run the targeted tests to verify they fail**

Run: `uv run pytest tests/test_vast.py -q`
Expected: FAIL because the current implementation uses `gpu_name: {"eq": "A100"}` and `gpu_ram: {"gte": 80}`.

**Step 3: Write the minimal implementation**

- Extend `GpuPreset` with explicit Vast GPU aliases.
- Update `search_offers()` to use `{"eq": alias}` for one alias or `{"in": aliases}` for multiple aliases.
- Convert GiB thresholds to MiB before sending `gpu_ram` to Vast.

**Step 4: Run the targeted tests to verify they pass**

Run: `uv run pytest tests/test_vast.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add app/config.py app/vast.py tests/test_vast.py
git commit -m "fix: use correct Vast GPU aliases and VRAM units"
```

### Task 2: Add preflight inventory diagnostics

**Files:**
- Modify: `app/service.py`
- Modify: `app/commands/up.py`
- Test: `tests/test_service.py`

**Step 1: Write the failing tests**

Add tests that assert:
- Preflight returns the first preset with offers when `auto` mode escalates.
- Preflight records each attempted preset and offer count.
- Empty results surface a message that distinguishes "no offers for minimum safe tier" from "no offers for any safe tier".

**Step 2: Run the targeted tests to verify they fail**

Run: `uv run pytest tests/test_service.py -q`
Expected: FAIL because no preflight summary object exists yet.

**Step 3: Write the minimal implementation**

- Add a small `InventoryCheck` result model in `app.service`.
- Factor the candidate-preset search into a reusable helper shared by preview and launch.
- Update `up` to print a compact preflight summary before attempting launch.

**Step 4: Run the targeted tests to verify they pass**

Run: `uv run pytest tests/test_service.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add app/service.py app/commands/up.py tests/test_service.py
git commit -m "feat: show Vast inventory preflight diagnostics"
```

### Task 3: Improve readiness progress and timeout messaging

**Files:**
- Modify: `app/vast.py`
- Modify: `app/commands/up.py`
- Test: `tests/test_vast.py`

**Step 1: Write the failing tests**

Add tests that assert:
- Progress callbacks receive staged messages including `actual_status` and a compact `status_msg`.
- Timeout errors include the last known stage/detail.

**Step 2: Run the targeted tests to verify they fail**

Run: `uv run pytest tests/test_vast.py -q`
Expected: FAIL because readiness output is too generic.

**Step 3: Write the minimal implementation**

- Normalize Vast status lines into short stage messages.
- Track the last meaningful status and include it in timeout exceptions.
- Keep the CLI output concise while still exposing the readiness phase.

**Step 4: Run the targeted tests to verify they pass**

Run: `uv run pytest tests/test_vast.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add app/vast.py app/commands/up.py tests/test_vast.py
git commit -m "feat: improve Vast readiness progress and timeout details"
```

### Task 4: Verify locally and against live Vast inventory

**Files:**
- Modify: `tests/test_vast.py`
- Modify: `tests/test_service.py`

**Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS

**Step 2: Run lint**

Run: `uv run ruff check`
Expected: PASS

**Step 3: Run a live inventory smoke check**

Run a short `uv run python` script that calls `VastAPI.search_offers()` for `1xa100-80gb` and `1xh100`.
Expected: non-zero counts when inventory exists, with valid offer IDs and prices.

**Step 4: Commit**

```bash
git add tests/test_vast.py tests/test_service.py
git commit -m "test: verify Vast inventory and readiness UX"
```
