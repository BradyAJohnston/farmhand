# 6n2y spin

farmhand test scene: PDB 6n2y as a surface with the Squishy material, doing one full turn over 120 frames. The rotation is built entirely in geometry nodes.

The scene is built locally with [Molecular Nodes](https://github.com/BradyAJohnston/MolecularNodes) and saved as a self-contained `.blend`. Molecular Nodes is not installed on Modal; the node groups and material are embedded in the file, so the render containers only need bpy, at the same version that saved the file (`bpy_version` in `pyproject.toml`).

This example depends on farmhand as an editable path, so clone the repo first. It needs Python 3.13 and downloads a bpy wheel of several hundred megabytes.

```sh
cd examples/6n2y-spin
uv sync
uv run scene.py --preview        # builds 6n2y_spin.blend; low-res preview frames in preview/
uv run farmhand config           # check environment / gpu / bpy_version / auth before spending money
uv run farmhand setup            # first time: Modal login and environment creation
uv run farmhand deploy           # once per config change
uv run farmhand render 6n2y_spin.blend --video 6n2y_spin.mp4
```

Output goes to `render/<job_id>/`: 120 PNG frames and `6n2y_spin.mp4`. At 1080p and 128 samples with `frames_per_container = 4`, that is 30 L40S containers and a few dollars of GPU time. For a cheap check first:

```sh
uv run farmhand render 6n2y_spin.blend --frame-end 2 --samples 16 --resolution-percentage 25
```
