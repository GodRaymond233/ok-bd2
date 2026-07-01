# Third-Party Notices

This project uses and references third-party open source projects. This file is
informational and does not replace the upstream license texts. If package
metadata and the upstream repository license disagree, treat the upstream
repository license as authoritative unless the upstream author clarifies
otherwise.

## Direct Runtime Dependencies

| Project | Use in this project | License / notice | Source |
|---|---|---|---|
| ok-script | Core GUI, task framework, capture, input, OCR integration, update helpers | Upstream GitHub repository contains AGPL-3.0 `LICENSE.txt`; PyPI metadata has also advertised an MIT classifier. This project treats redistributed ok-script code as AGPL-3.0 unless upstream clarifies otherwise. | https://github.com/ok-oldking/ok-script |
| adbutils | ADB support through ok-script/device workflows | MIT | https://github.com/openatx/adbutils |
| comtypes | Windows COM support | MIT | https://github.com/enthought/comtypes |
| numpy | Array processing used by tests and image matching helpers | BSD-3-Clause with bundled third-party notices | https://github.com/numpy/numpy |
| onnxocr-ppocrv5 | OCR inference pipeline | Apache-2.0 | https://github.com/ok-oldking/OnnxOCR |
| OpenCC | Chinese text conversion | Apache-2.0 | https://github.com/BYVoid/OpenCC |
| opencv-python | Image processing and template matching | Apache-2.0 | https://github.com/opencv/opencv-python |
| OpenVINO | OCR/model runtime acceleration | Apache-2.0 | https://github.com/openvinotoolkit/openvino |
| Pillow | Image file handling | MIT-CMU | https://github.com/python-pillow/Pillow |
| psutil | Process inspection | BSD-3-Clause | https://github.com/giampaolo/psutil |
| PyDirectInput | Windows input simulation | MIT | https://github.com/learncodebygaming/pydirectinput |
| pynput | Input monitoring/control support | LGPL-3.0 | https://github.com/moses-palmer/pynput |
| PySide6 | Qt UI binding | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only, with commercial licensing available from Qt | https://code.qt.io/cgit/pyside/pyside-setup.git |
| PySide6-Fluent-Widgets | Fluent UI widgets | GPL-3.0 | https://github.com/zhiyiYo/PyQt-Fluent-Widgets/tree/PySide6 |
| pywin32 | Windows API bindings | PSF License | https://github.com/mhammond/pywin32 |

## Packaging And Update Components

The release workflow may inline or package additional components into the update
repository or installer artifacts. When distributing those artifacts, keep the
corresponding upstream license and source references available.

| Project | Use in this project | License / notice | Source |
|---|---|---|---|
| pyappify | Launcher/update UI and packaging flow | PyPI metadata advertises MIT. Preserve upstream notices when bundled. | https://github.com/ok-oldking/pyappify |
| ok-oldking/pyappify-action | GitHub Action used to build installer assets | Preserve upstream notices if action output bundles project code. | https://github.com/ok-oldking/pyappify-action |
| ok-oldking/partial-sync-repo | GitHub Action used to sync the update repository | Build-time action; not an application runtime dependency. | https://github.com/ok-oldking/partial-sync-repo |
| softprops/action-gh-release | GitHub Release publishing | Build-time action; not an application runtime dependency. | https://github.com/softprops/action-gh-release |
| signpath/github-action-submit-signing-request | Optional signing workflow | Build-time action; not an application runtime dependency. | https://github.com/signpath/github-action-submit-signing-request |

## Important Transitive Dependencies

The packages above install additional dependencies. The exact transitive set can
change with package versions. The commonly installed runtime dependencies include
`requests` (Apache-2.0), `typing-extensions` (PSF-2.0), `mouse` (MIT), `pycaw`,
`pyclipper` (MIT), `shapely` (BSD-3-Clause), `darkdetect` (BSD-3-Clause),
`PySideSix-Frameless-Window` (LGPL-3.0), `openvino-telemetry` (Apache-2.0),
`certifi` (MPL-2.0), `charset-normalizer` (MIT), `idna` (BSD-3-Clause),
`urllib3` (MIT), and `packaging` (Apache-2.0 OR BSD-2-Clause).

## Reference Projects

The project structure and release flow were developed with reference to the
following public projects. They are credited for reference and learning; source
files from these projects should not be copied into this repository unless their
licenses and attribution requirements are also preserved.

| Project | How it was used | Source |
|---|---|---|
| BnanZ0/ok-nte | Reference for current ok-script project layout, README structure, `pyproject.toml`, tasks, scene/interaction layout, and PyAppify release shape | https://github.com/BnanZ0/ok-nte |
| ok-oldking/ok-script-app | Reference starter template for ok-script applications, example tasks, custom tabs, i18n, tests, and packaging files | https://github.com/ok-oldking/ok-script-app |
| ok-oldking/ok-wuthering-waves | Reference for mature ok-script game automation structure, trigger task registration, custom tabs, logging, and update packaging | https://github.com/ok-oldking/ok-wuthering-waves |
| sunyink/MFABD2 | Local ignored upstream reference for BrownDust II automation ideas and README/credit style. Do not copy code or assets without preserving its license notices. | https://github.com/sunyink/MFABD2 |

## Game And Platform Materials

BrownDust II names, UI screenshots, icons, and template images belong to their
respective rights holders. This project uses them only for image recognition,
testing, and documentation of this automation tool.
