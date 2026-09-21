"""Build the 6n2y spin scene with Molecular Nodes and save it as a .blend for farmhand.

    uv run scene.py            # writes 6n2y_spin.blend
    uv run scene.py --preview  # also renders a few low-res frames to preview/

The rotation lives entirely in the geometry node tree: the atoms are centred on the
world origin, rotated about Z by an animated angle, then styled as a surface.
"""

import math
import sys
from pathlib import Path

import bpy
import molecularnodes as mn
from molecularnodes.nodes import geometry as g
from nodebpy import geometry as ng

HERE = Path(__file__).parent
BLEND = HERE / "6n2y_spin.blend"
FRAMES = 120  # one full turn; reaching tau at FRAMES + 1 makes it loop seamlessly

canvas = mn.Canvas(engine=mn.scene.Cycles(samples=128), resolution=(1920, 1080))
canvas.frame_range = (1, FRAMES)

mol = mn.Molecule.fetch("6n2y", cache=HERE / "data")
squishy = mn.material.Squishy().material

with mol.tree.reset() as (atoms, join):
    angle = g.AnimateValue(frame_start=1, frame_end=FRAMES + 1, value_min=0.0, value_max=math.tau)
    (
        atoms
        >> g.CentreOnSelection()
        >> ng.TransformGeometry(rotation=ng.CombineXYZ(z=angle))
        >> g.StyleSurface(material=squishy)
        >> join
    )

# Frame the molecule with room for its silhouette to change as it turns.
canvas.look_at(mol, viewpoint="front", margin=0.2)

bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
print(f"saved {BLEND} ({BLEND.stat().st_size / 1e6:.1f} MB), frames 1-{FRAMES}")

if "--preview" in sys.argv:
    preview = HERE / "preview"
    preview.mkdir(exist_ok=True)
    canvas.engine = mn.scene.Cycles(samples=16, device="CPU", denoise=False)
    for frame in (1, 30, 60, 90):
        canvas.snapshot(preview / f"frame_{frame:03d}.png", frame=frame, render_scale=25)
        print(f"preview frame {frame} written")
