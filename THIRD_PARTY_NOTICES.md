# Third-Party Notices

This project uses and references third-party projects. This file is informational
and does not replace the license texts supplied with the specific versions used.

## Direct Runtime Dependencies

| Project | Use in this project | License / notice | Source |
|---|---|---|---|
| ok-script 3.0.15b1 | Task framework, capture, input, OCR integration, update helpers | ok-script License: Apache License 2.0 as modified by Commons Clause and additional terms. The package's complete notice is preserved in `LICENSES/ok-script-3.0.15b1.txt`. | https://github.com/ok-oldking/ok-script |
| ok-script Web runtime (copy) | `src/desktop/okweb` is a copy of the Web runtime of ok-script 2.0.6, which later versions removed; it serves the desktop client | `LICENSES/ok-script-2.0.6.txt` | https://github.com/ok-oldking/ok-script |
| adbutils | ADB support through ok-script/device workflows | MIT | https://github.com/openatx/adbutils |
| comtypes | Windows COM support | MIT | https://github.com/enthought/comtypes |
| msvc-runtime | Installs Microsoft Visual C++ runtime DLLs beside the packaged Python runtime | Proprietary Microsoft redistributable components. The wheel includes the Visual Studio 2022 redistribution notice; release builds must preserve the package notice and comply with Microsoft's redistribution terms. | https://pypi.org/project/msvc-runtime/ |
| numpy | Array processing used by tests and image matching helpers | BSD-3-Clause with bundled third-party notices | https://github.com/numpy/numpy |
| onnxocr-ppocrv6 | OCR inference pipeline with the bundled PP-OCRv6 small models (PaddlePaddle, Apache-2.0) | Apache-2.0 | https://github.com/ok-oldking/OnnxOCR |
| OpenCC | Chinese text conversion | Apache-2.0 | https://github.com/BYVoid/OpenCC |
| opencv-python | Image processing and template matching | Apache-2.0 | https://github.com/opencv/opencv-python |
| OpenVINO | OCR/model runtime acceleration | Apache-2.0 | https://github.com/openvinotoolkit/openvino |
| Pillow | Image file handling | MIT-CMU | https://github.com/python-pillow/Pillow |
| psutil | Process inspection | BSD-3-Clause | https://github.com/giampaolo/psutil |
| PyDirectInput | Windows input simulation | MIT | https://github.com/learncodebygaming/pydirectinput |
| pynput | Input monitoring/control support | LGPL-3.0 | https://github.com/moses-palmer/pynput |
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
`urllib3` (MIT), `packaging` (Apache-2.0 OR BSD-2-Clause), and `dulwich`
(Apache-2.0 OR GPL-2.0-or-later, used by pyappify).

## Reference Projects

The project structure and release flow were developed with reference to the
following public projects. They are credited for reference and learning; source
files from these projects should not be copied into this repository unless their
licenses and attribution requirements are also preserved.

The task scheduler's persisted next-run and retry behavior was developed with
reference to ALAS/NKAS scheduling semantics.

| Project | How it was used | Source |
|---|---|---|
| BnanZ0/ok-nte | Reference for current ok-script project layout, README structure, `pyproject.toml`, tasks, scene/interaction layout, and PyAppify release shape | https://github.com/BnanZ0/ok-nte |
| ok-oldking/ok-script-app | Reference starter template for ok-script applications, example tasks, custom tabs, i18n, tests, and packaging files | https://github.com/ok-oldking/ok-script-app |
| ok-oldking/ok-wuthering-waves | Reference for mature ok-script game automation structure, trigger task registration, custom tabs, logging, and update packaging | https://github.com/ok-oldking/ok-wuthering-waves |
| JZPPP/MaaBD2 | Reference for BrownDust II map-collection navigation ideas. | https://github.com/JZPPP/MaaBD2 |
| sunyink/MFABD2 | Reference for field-encounter escape, navigation result branches, bounded retries, HUD-based skill exhaustion and per-item sale retention; implemented with this project's recognition, input and collection accounting. Also consulted for recognizing a truncated dismantle button label. | https://github.com/sunyink/MFABD2 |
| nobell001/YES-BD2 | Reference for collection and portal recovery, observed skill capacity, selectable cooking batches and visible shop-cartridge selection; implemented with this project's recognition, input and consumption guards. Also consulted for retrying entry from a confirmed equipment bag and dismissing a result overlay outside the equipment grid. | https://github.com/nobell001/YES-BD2 |
| BD2DB | Public market data used once to seed the bundled 1-28 day price snapshot. The runtime does not query this site. | https://browndust2-db.souseha.com/en/market-data |

The six cooking templates for Kello Wellness Soup, Saffron Sweet Tea, Fruit
Smoothie, Chocolate Bread, Dark Beer Stew and Potato Soup under
`recognition-assets/template-assets/image/Shop/` are unmodified assets from
nobell001/YES-BD2 commit `0b58668ce715cd2f38233a9b4ab35563ea40937a`
(GPL-3.0, see `LICENSE`). Its contributors captured the game UI; underlying
game-art rights remain with the respective rights holders. They use ok-bd2's
existing template scaling and recognition regions.

The Salmon Wasabi Sushi template is cropped and resized from the in-game
recipe list screenshot `cooking_list_cn_2k_20261110.jpg` in the same YES-BD2
repository at commit `4ab3e152e69a675e7e48b8e566244a616dc652bb` under
the same terms. The recipe priority guide
image is excerpted from the chart by 西巧克力大道 supplied by the maintainer.

The pass-reward badge templates are cropped from the PC screenshot fixture
`tests/fixtures/pass/pass_page_badges_fhd.png` in nobell001/YES-BD2
commit `4ab3e152e69a675e7e48b8e566244a616dc652bb` (GPL-3.0).
The underlying game art remains property of its rights holders.

`doom_field_swords.png` is a binary recognition mask derived from the PC capture
`tests/fixtures/doom/field_red_costume_1440_as_1080.png` in nobell001/YES-BD2
commit `0b58668ce715cd2f38233a9b4ab35563ea40937a` (GPL-3.0).
`doom_card_cover.png` is the `recognition-assets/template-assets/doom/last_night_card.png`
recognition template from the same revision. The underlying game art remains
property of its rights holders.

`tower_cartridge_cover.png` and `tower_level_tick.png` are recognition templates
from the `evil_castle` assets in the same YES-BD2 revision. `tower_spawn_steps.png`
and `tower_altar_marker.png` are derived from the PC screenshots
`field_spawn_2k.jpg` and `field_altar_marker_2k.jpg` in `tests/fixtures/redemption_tower`.

`event_reward_hand.png` is the YES-BD2 recognition template
`recognition-assets/template-assets/event/reward_hand.png` from commit
`0b58668ce715cd2f38233a9b4ab35563ea40937a` (GPL-3.0).

## Game And Platform Materials

BrownDust II names, UI screenshots, icons, and template images belong to their
respective rights holders. This project uses them only for image recognition,
testing, and documentation of this automation tool.
