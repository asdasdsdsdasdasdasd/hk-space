# hk-space — layout engine for small Hong Kong flats

Whole-flat spatial optimisation for 200–500 sq ft flats: read a floor plan, decide where every piece of
furniture goes, score how spacious each room actually *feels*, render the result, and let an LLM advisor
argue with the geometry — while the geometry always has the last word.

Design doc: [`docs/hk-space-ai-tech-plan-v3.md`](docs/hk-space-ai-tech-plan-v3.md) (v3.2).
The original Chinese lab notebook (render iterations, bug hunts, the Farnsworth House comparison) is kept
verbatim in [`docs/README.zh.md`](docs/README.zh.md).

## What this proves

**‘Feels spacious’ is computable, and CP-SAT can optimise it.** Same five pieces of furniture, nothing
removed: move the bulky pieces into the low-exposure pockets of an L-shaped room, clear the sightline from
door to window, swap the dark wardrobe for a pale open one — spaciousness **48 → 87 → 92**
(`out/poster.png`).

## Pipeline

```text
floor plan (scan / phone photo)
  └─ services/floorplan/vectorize.py    OpenCV only: walls, openings, room polygons,
                                        scale recovered from printed dimension numbers
       → Room JSON (integer millimetres)
       → hkgeometry.house.flat_from_dict
            → solve_house()
                 L1  room assignment          CP-SAT, 17/17 items in 0.13 s
                 L2  per-room placement       CP-SAT, one worker per room (~41 s)
                 L3  cross-room validation    reachability + door-swing intrusion (0 cm²)
            → livability.py · spaciousness.py · bench_hk.check()
                 verdict: 15/16 checks, per-room spaciousness, JSON detractors
            → render_plan2d.py / render_dollhouse.py / blender_dollhouse.py
                 2D plan, exposure heat, dollhouse PNG, GLB
            → apps/web (live console)  +  llm_advisor.py (advice loop and chat)
```

## How it works

### 1. Read the flat

`services/floorplan/vectorize.py` (1.5k lines, OpenCV, no trained model, no dataset): deskew, detect
bearing walls (solid black, ~200 mm) versus partitions (thin double lines, ~100 mm), recover openings, and
recover the scale from the **printed dimension numbers** — the drawing’s own claim about physical size —
with a template-matching OCR fallback (pytesseract is optional). When no numbers are readable it falls back
to ‘bearing wall ≈ 200 mm’, then to `--assume-scale`. Every run reports `meta.ocr_engine` and a confidence
breakdown. Room IoU against ground truth is **0.94–0.99** on the three test plans in `data/floorplans/`.

### 2. Model the problem

- **Integer millimetres, everywhere** (`units.py`, frozen). No floats in geometry, no unit conversion
  except at the rendering edge. Angles are `0 / 90 / 180 / 270`.
- A `Placement` is `inst, sku, category, x, y, rot, w, d, h`; `x, y` is the **min corner**; the footprint
  swaps `w`/`d` at 90/270; front clearance is derived from `category`, not authored per item.
- Visual mass is a four-factor product compressed with a **0.45 power**: the raw spread is 79×, compressed
  to 7.2×, otherwise the optimiser only ever cares about the single biggest cabinet (`types.py`).

### 3. Solve

`house.solve_house()` runs three levels: assign items to rooms (CP-SAT, respects `CATEGORY_ROOMS`), solve
each room independently in parallel, then validate across rooms. A greedy + local-search baseline is kept
(`greedy.py`) because it fails in a revealing way: on a long room it *regresses* after optimisation (80 → 70)
— a bad basin — where CP-SAT does not (67 → 78).

| room | greedy + local search | CP-SAT |
|---|---|---|
| demo (7.5 m², L-shaped) | 83 → 91 | **48 → 87 → 92** |
| e2e (15.2 m², long strip) | 80 → **70** ❌ regression | **67 → 78** ✅ |

### 4. Judge

- `livability.py` — hard geometric rules: front clearance, walkway, door swing, wall contact, reachability.
- `spaciousness.py` — exposure-based score plus machine-readable detractors and fix options.
- `bench_hk.py` — the acceptance harness (task program, doors, fit, overlap, in-room containment, reachable
  fronts, corridor width). The web console shows these rows **verbatim**; it never re-derives them.

### 5. Draw

2D plan with walls at real thickness, real door openings with swing arcs, triple-line windows and room
labels; exposure heat map; 3D dollhouse via Blender headless; GLB export.

### 6. Converse

`llm_advisor.py` — **the LLM never emits coordinates.** It picks actions (`add_item`, `define_custom`,
`shrink_sku`, `move_item`, `drop_item`, `try_nudge`, `keep`); deterministic code applies them
(`apply_decisions`), then the engine re-solves and re-checks. Feasibility is always the geometry’s verdict,
never the model’s. The same machinery backs the web chat.

## Three rules that keep it honest

1. **One unit.** Everything is integer millimetres, in memory and over the wire. Display converts; the
   model does not.
2. **One ruler.** The UI must never implement a criterion of its own. Every pass/fail and every score comes
   from `bench_hk.check()`, `livability`, `spaciousness` or `overlays_json()`. The historical trap: a
   self-made `min_corridor_width` in the front end disagreed with the bench ruler, and the product looked
   broken when the engine was right.
3. **Labels must not lie.** `解算` (solve) is solver time only; `即時核查` (live verify) is the whole
   verify / repair / import request; `傾偈` (chat) is one LLM call. A request that solved nothing never shows
   a solve label. An item the engine could not place is reported as such — including items added by chat.

## Module map

| path | role |
|---|---|
| `packages/geometry/hkgeometry/` | geometry kernel: rooms, placements, isovist, exposure, corridor, livability, spaciousness, program, cp-sat / greedy / progressive solvers, house decomposition, LLM advisor, visual eval |
| `packages/templates/` | 30 parametric furniture templates → GLB |
| `services/floorplan/` | raster floor plan → Room JSON (OpenCV), PDF extraction (needs PyMuPDF), test-plan generator |
| `data/catalog/` | ~160 SKU seed rows (IKEA HK style) with visual attributes |
| `data/floorplans/` | three synthetic test plans + ground truth |
| `scripts/` | demos, benchmarks (`bench_hk.py`), renderers, posters, Farnsworth comparison |
| `apps/web/` | zero-dependency live console: solve, drag, re-verify, auto-repair, export/import, chat |
| `docs/` | design doc, placement audit, benchmarks, the Chinese lab notebook |

## Results (measured, not aspirational)

Whole flat — `plan_hk_a`, 39 m², 5 rooms, 23 items including fixed fixtures:

| level | result |
|---|---|
| L1 room assignment | 17/17 items, **0.13 s** (CP-SAT) |
| L2 in-room placement | 5 rooms in parallel, 41 s |
| L3 cross-room validation | reachability all clear, corridor intrusion into door zones 0 cm² |

Per-room livability: living 100 / bath 100 / storage 100 / bedroom 78 / kitchen 56; spaciousness 77–99.

Selection-only optimisation (positions frozen, styles swapped from the catalogue): total price
**$8,750 → $5,909 (−$2,841)** while spaciousness *rose* 5 points. E.g. wardrobe PAX 83 pts $4,490 versus
VUKU 81 pts $790.

The catalogue gap this exposes is a product finding, not a bug: a 1,400 × 700 sofa slot against IKEA HK’s
smallest 1,680 × 920 sofa is a **500 mm** miss — which is exactly why cross-retailer comparison and custom
furniture belong in the product.

LLM advisor loop: on both flats the hard violations reach **0**, with every decision and its reason kept in
`out/llm_trace*.json`.

Web console (`onpak05`): **15/16** checks, the only failure being `9 柜门/抽屉打得开 ['#toilet被#shower挡']`;
`stress_tiny` and `stress_narrow` at 12/12. Live verify ~1 s, chat 2–6 s of LLM plus 1–4 s of re-solve.

## Quick start

```bash
# dependencies (CP-SAT comes from ortools; PyMuPDF only if you feed PDFs)
pip install shapely trimesh manifold3d numpy matplotlib opencv-python pillow ortools

# 1. spaciousness demo (the core claim)
cd packages/geometry && python3 hkgeometry/demo.py && cd ../..

# 2. selection-only optimisation
python3 scripts/demo_selection.py

# 3. 3D dollhouse (Blender headless, ~40 s)
python3 scripts/render_dollhouse.py

# 4. poster: 3D + 2D side by side
python3 scripts/make_poster.py

# 5. end to end: floor plan → vectorise → selection → placement → 3D
python3 services/floorplan/make_test_floorplan.py
python3 services/floorplan/vectorize.py --image data/floorplans/plan_hk_a.png --out out/plan_hk_a.json
python3 scripts/demo_end2end.py && python3 scripts/make_poster_e2e.py

# 6. LLM advisor loop (DeepSeek API by default; key from HKSPACE_LLM_KEY /
#    DEEPSEEK_API_KEY / ~/.dsh/.credentials.yaml — never written to the repo, logs or prompts)
python3 scripts/demo_llm_advisor.py --max-rounds 4
python3 scripts/demo_llm_advisor.py --plan out/plan_hk_b.json --tag b
#    or point it at a local llama.cpp server:
python3 scripts/demo_llm_advisor.py --local --max-rounds 4

# 7. web console, no extra dependencies (stdlib HTTP server + hand-written canvas)
python3 -u apps/web/server.py --port 8791
#    → http://127.0.0.1:8791
#    pick a case → solve → drag furniture → live re-verify → auto-repair → export/import
#    the chat on the right takes one sentence and regenerates the layout
```

Artifacts land in `out/` (2D plan, exposure heat map, isovist, spaciousness cards).

## Web console (`apps/web/`)

Python standard library plus a hand-written canvas front end — no framework, no bundler, no CDN:

- **Solve** with `auto` / `cp-sat` / `progressive`, time limit, variant; overlay toggles for base image,
  exposure heat, front clearance, door swing zones, sightlines, the 900 mm artery, labels, 500 mm grid.
- **Edit** by dragging, nudging (50 mm, Shift 250 mm), rotating (`R`), deleting (`⌫`), undoing (`Z`);
  every edit re-verifies through `/api/verify` after a 350 ms debounce — the engine judges, the canvas only
  paints.
- **Auto-repair** nudges only the stuck items (`try_nudge`) and prints the engine’s own notes.
- **Export / import** a layout JSON; import goes back through `/api/verify`, so a hand-edited layout is
  judged by the engine and not by the editor.
- **Chat** (‘同設計師講’): one sentence → the LLM picks actions → deterministic code applies them → the
  engine re-solves and re-checks. Refusals (a wardrobe may not enter a living room, per `CATEGORY_ROOMS`)
  and unplaceable additions are stated, not hidden.

Details, endpoint table and measured round trips: [`apps/web/README.md`](apps/web/README.md).

## What is not done

- Accounts, persistence, multi-scheme (Pareto) comparison, a cost model, collaboration.
- A test suite (no pytest configuration anywhere; `bench_hk.py` is the current acceptance harness).
- Uploading **your own** floor plan in the console — the page serves three built-in cases; the vectoriser
  exists and produces a compatible Room JSON, but the upload → solve path is not wired.
- PDF plans: `services/floorplan/pdf_extract.py` needs PyMuPDF, which is not installed here.
- Human perception calibration of the visual target. Thresholds are geometry-calibrated, not
  human-validated — which is why the plan orders the visual objective (M1) before training (M3).

## Data, provenance and third-party files

- `out/ref/*.pdf` are Hong Kong Housing Authority sales brochures kept as source evidence. They remain the
  copyright of their publisher and are **not** covered by this repository’s licence; drop them with
  `git rm -r out/ref` if you do not want to redistribute them.
- `data/floorplans/plan_hk_{a,b,c}.png` are synthetic test plans generated by
  `services/floorplan/make_test_floorplan.py`, with ground truth beside them.
- `data/catalog/` is a small hand-curated sample of IKEA-HK-style SKUs for demos; prices are approximate.
- API keys are read from the environment or a local credentials file and are never written into the repo,
  logs or prompts.

## Licence

MIT — see [`LICENSE`](LICENSE).
