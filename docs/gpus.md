# Choosing a GPU

Set `gpu` in `[tool.farmhand]`, `farmhand.yml`, or `farmhand deploy --gpu`. A list is a fallback order: Modal tries each in turn when the first is unavailable.

```toml
[tool.farmhand]
gpu = ["RTX-PRO-6000", "L40S"]
```

## What Cycles actually needs

In practice Cycles path tracing scales with FP32 shader throughput and, under OptiX, with hardware ray-tracing cores. Memory bandwidth and capacity only matter once the scene does not fit. A typical Molecular Nodes scene uses a few gigabytes of VRAM at most, so the 80 to 288 GB cards buy nothing.

The AI-oriented cards (A100, H100, H200, B200, B300) have no RT cores and trade FP32 units for tensor cores and HBM. The visualisation cards (T4, L4, L40S, RTX PRO 6000) have RT cores and much higher FP32 throughput per dollar. The table below bears this out: for rendering, the visualisation cards are both faster and cheaper.

## The numbers

Modal price is on-demand, per second, as of 2026-09-21. Score is the Blender Open Data median in samples per minute for that device, all submitted Blender versions and compute types pooled. Per dollar is score divided by hourly price.

| Modal `gpu=` | VRAM | $/hour | Score (samples/min) | Runs | vs L40S | Per dollar |
|---|---|---|---|---|---|---|
| `RTX-PRO-6000` | 96 GB | 3.03 | 15,640 | 46 | 1.72x | 5,160 |
| `L40S` | 48 GB | 1.95 | 9,115 | 60 | 1.00x | 4,670 |
| `L4` | 24 GB | 0.80 | 3,623 | 51 | 0.40x | 4,530 |
| `A10` | 24 GB | 1.10 | 3,245 | 37 | 0.36x | 2,950 |
| `T4` | 16 GB | 0.59 | 1,459 | 147 | 0.16x | 2,470 |
| `A100-40GB` | 40 GB | 2.10 | 3,647 | 22 | 0.40x | 1,740 |
| `H100` | 80 GB | 3.95 | 6,049 | 4 | 0.66x | 1,530 |
| `A100-80GB` | 80 GB | 2.50 | 3,750 | 10 | 0.41x | 1,500 |
| `H200` | 141 GB | 4.54 | 6,269 | 3 | 0.69x | 1,380 |
| `B200` | 180 GB | 6.25 | 8,311 | 10 | 0.91x | 1,330 |
| `B300` | 288 GB | 7.10 | no data | 0 | | |

Caveats on the scores. Open Data pools every submission for a device, so older Blender versions and CUDA runs drag the medians down a little, and the H100 and H200 medians rest on three or four runs. Treat the split between the two families as solid and the ratios within a family as approximate. Modal's `A10` is the A10G-class card, so the row uses Open Data's `A10G` entry; the plain `A10` entry scores 2,781 over 20 runs. B300 has no Open Data entry and is assumed to behave like the B200.

## Recommendations

- **Default: `L40S`.** Second-fastest card, best value after the RTX PRO 6000, widely available, and 48 GB is more than any Molecular Nodes scene will need.
- **Fastest: `RTX-PRO-6000`.** Blackwell generation with RT cores. About 1.7x the L40S for 1.55x the price, so it is also the cheapest per sample. Worth putting first in a fallback list, with `L40S` behind it in case of capacity limits.
- **Cheapest for small or preview renders: `L4`.** About the same per-dollar efficiency as the L40S at 40 percent of the speed. Good when you have many light frames and wall time does not matter.
- **Avoid for rendering: `A100`, `H100`, `H200`, `B200`, `B300`.** Roughly three times worse per dollar than the L40S and slower in absolute terms, except the B200 which is roughly L40S speed at three times the price.
- **`T4` and `A10`** are superseded by the L4 on speed and on cost per sample. The T4 is cheaper per hour, but you get less for it.

## Fixed per-container overhead

Each container pays a fixed cost before it renders anything: boot, bpy import, opening the blend, and evaluating the geometry nodes. For a Molecular Nodes surface that can be a minute or more, and it is CPU-bound, so a faster GPU does not shrink it. Three consequences:

- Raise `frames_per_container` so that overhead is amortised over more frames. Four to ten is a reasonable range for frames that render in under a minute each.
- `timeout` (default 7200 seconds) is per container, so `frames_per_container` times the per-frame time must stay under it.
- A faster GPU only pays off when the render step dominates. For quick low-sample frames, the L4 wins on cost; for heavy frames at high sample counts, the RTX PRO 6000 or L40S win on both cost and wall time.

## Modal quirks

- Modal may silently upgrade `H100` to `H200` and `A100` to `A100-80GB` at no extra cost. `H100!` pins the H100. Neither matters for rendering.
- `gpu` is baked in at `farmhand deploy`. Changing it in config needs a redeploy.
- `gpu: null` (CPU only) is valid for Modal but farmhand's Cycles GPU setup will fail; use a GPU.

## Sources

[Modal pricing](https://modal.com/pricing), [Modal GPU guide](https://modal.com/docs/guide/gpu), and the per-device pages on [Blender Open Data](https://opendata.blender.org/), all read on 2026-09-21.
