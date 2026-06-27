# Architecture

The project mirrors the reusable parts of the `ok-nte` skeleton and the
`ok-script-app` template, while keeping BD2-specific logic empty.

## Runtime flow

1. `main.py` loads `src.config.config`.
2. `ok.OK(config)` creates the GUI, capture device, task executor, and scene.
3. Tasks from `src/tasks` run through ok-script and share `BD2Scene`.
4. Template matching uses `assets/coco_annotations.json` and `src/Labels.py`.

## Extension points

- Add game state helpers to `src/scene/BD2Scene.py`.
- Add shared task helpers to `src/tasks/BaseBD2Task.py`.
- Add input quirks to `src/interaction/BD2Interaction.py`.
- Add template post-processing rules to `src/process_feature.py`.
- Register new tasks in `src/config.py`.

## Probe Task

`src.tasks.BD2ProbeTask` is the baseline integration check for future work. It
captures a frame through the configured capture method, saves a screenshot, runs
OCR, and writes the recognized text to `probe_outputs/bd2_probe_ocr_latest.txt`.
`BaseBD2Task.capture_frame()` saves probe screenshots synchronously so command
line runs and GUI runs produce the same artifacts.

Windows Graphics Capture is the preferred background capture method. The local
compatibility shim in `src.compat.windows_graphics` enables WGC on supported
Windows 10 builds where upstream ok-script's availability check is too strict.

## Reference Shape

- `ok-nte`: current `pyproject.toml` project layout with `src/tasks`,
  `src/interaction`, `src/scene`, template labels, and `pyappify.yml`.
- `ok-script-app`: starter template with a visible one-time task, trigger-task
  example, custom tab example, `requirements.in`, and packaging files.
- `ok-wuthering-waves`: mature game project showing global config groups,
  trigger task registration, custom tabs, logs, and update packaging.
