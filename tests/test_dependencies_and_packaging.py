from __future__ import annotations

import io
import json
import logging
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import Mock, patch

import numpy as np
from onnxocr.predict_base import PredictBase, _cache_is_current, _model_cache_path

from src.compat import dependency_guard as guard


class EnsureCoreDependenciesTest(unittest.TestCase):
    def test_missing_module_repairs_from_locked_version_and_fallback_index(self):
        import importlib.util

        find_spec = importlib.util.find_spec
        with patch(
            "importlib.util.find_spec",
            side_effect=lambda name: None if name == "win32con" else find_spec(name),
        ):
            self.assertEqual(["win32con"], guard.find_missing_modules())
        locked = guard.parse_locked_versions("# via BD2\npywin32==311 ; sys_platform == 'win32'\n")
        for repaired in (False, True):
            missing = [["win32con"], [] if repaired else ["win32con"]]
            attempts = [
                subprocess.CompletedProcess([], code, "", "") for code in (1, int(not repaired))
            ]
            with (
                self.subTest(repaired=repaired),
                patch.object(guard, "find_missing_modules", side_effect=missing),
                patch.object(guard, "read_locked_versions", return_value=locked),
                patch.object(guard.subprocess, "run", side_effect=attempts) as install,
                patch.object(guard, "_notify") as notify,
                self.assertRaises(SystemExit) as exit_error,
            ):
                guard.ensure_core_dependencies()
            self.assertEqual(int(not repaired), exit_error.exception.code)
            command = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                "--no-deps",
                "pywin32==311",
            ]
            self.assertEqual(
                [command + ["--index-url", guard.PIP_INDEX_FALLBACK[0]], command],
                [call.args[0] for call in install.call_args_list],
            )
            self.assertEqual(not repaired, notify.call_args.kwargs.get("error", False))


ROOT = Path(__file__).resolve().parents[1]


class DependencyManagementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cls.lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        cls.runtime_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        cls.dev_requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    def test_runtime_pins_match_both_exports_and_lock(self):
        project = self.pyproject["project"]["dependencies"]
        locked = {p["name"]: p["version"] for p in self.lock["package"]}
        for name, spec, version in (
            ("ok-script", "ok-script[ocr]==3.0.14", "3.0.14"),
            ("onnxocr-ppocrv6", "onnxocr-ppocrv6==0.0.1", "0.0.1"),
            ("pyappify", "pyappify==1.0.20", "1.0.20"),
            ("msvc-runtime", "msvc-runtime>=14.44.35112", "14.44.35112"),
            ("openvino", "openvino==2026.4.1", "2026.4.1"),
        ):
            with self.subTest(package=name):
                self.assertIn(spec, project)
                self.assertEqual(version, locked[name])
                for export in (self.runtime_requirements, self.dev_requirements):
                    self.assertIn(f"{name}=={version}", export)
        self.assertNotIn("onnxocr-ppocrv5", locked)
        self.assertNotIn("onnxocr-ppocrv5", self.runtime_requirements)


class _FakeInput:
    any_name = "image"

    @staticmethod
    def get_partial_shape():
        return "dynamic"


class _FakeModel:
    inputs = [_FakeInput()]

    def reshape(self, _shapes):
        return None


class _FakeOutput:
    @staticmethod
    def get_any_name():
        return "output"


class _FakeCompiledModel:
    outputs = [_FakeOutput()]


class _FakeTensor:
    def __init__(self, data):
        self.data = data


class _FakeInferRequest:
    def __init__(self, data):
        self.output_tensors = [_FakeTensor(data)]


class _FakeAsyncInferQueue:
    instances = []

    def __init__(self, session, jobs=1):
        self.session = session
        self.jobs = jobs
        self.callback = None
        self.submissions = []
        self.__class__.instances.append(self)

    def __len__(self):
        return self.jobs

    def set_callback(self, callback):
        self.callback = callback

    def start_async(self, input_feed, *, userdata, share_inputs):
        self.submissions.append((input_feed, userdata, share_inputs))
        result = np.asarray([[42.0]], dtype=np.float32)
        self.callback(_FakeInferRequest(result), userdata)


class _FakeCore:
    available_devices = ["CPU"]
    cache_properties = []
    compile_configs = []

    def set_property(self, properties):
        self.__class__.cache_properties.append(properties)

    @staticmethod
    def read_model(*, model):
        return _FakeModel()

    @staticmethod
    def compile_model(*, model, device_name, config):
        _FakeCore.compile_configs.append((device_name, config))
        return _FakeCompiledModel()


def _fake_openvino_module():
    return types.SimpleNamespace(Core=_FakeCore, AsyncInferQueue=_FakeAsyncInferQueue)


class OnnxOcrRuntimeTest(unittest.TestCase):
    def test_async_infer_queue_default_and_explicit_jobs_are_bounded(self):
        _FakeAsyncInferQueue.instances.clear()
        _FakeCore.cache_properties.clear()
        _FakeCore.compile_configs.clear()
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            mock.patch.dict(sys.modules, {"openvino": _fake_openvino_module()}),
            mock.patch.object(Path, "cwd", return_value=Path(temp_dir)),
        ):
            default = PredictBase(
                "det.onnx",
                use_openvino=True,
                use_npu=False,
                logger=logging.getLogger("test-onnxocr"),
            )
            explicit = PredictBase(
                "rec.onnx",
                use_openvino=True,
                use_npu=False,
                logger=logging.getLogger("test-onnxocr"),
                openvino_num_requests=2,
                openvino_cpu_cache_capacity=8,
            )

            self.assertEqual([1, 2], [queue.jobs for queue in _FakeAsyncInferQueue.instances])
            self.assertEqual(2, len(explicit._async_queue))
            self.assertEqual(
                [
                    ("CPU", {"CPU_RUNTIME_CACHE_CAPACITY": "16"}),
                    ("CPU", {"CPU_RUNTIME_CACHE_CAPACITY": "8"}),
                ],
                _FakeCore.compile_configs,
            )
            self.assertTrue((Path(temp_dir) / "cache" / "openvino").is_dir())
            self.assertEqual(
                {"CACHE_DIR": str(Path(temp_dir) / "cache" / "openvino")},
                _FakeCore.cache_properties[-1],
            )

            output = default._run_openvino_async(
                {"image": np.zeros((1, 3, 2, 2), dtype=np.float32)}
            )
            self.assertEqual((1, 1), output[0].shape)
            self.assertEqual(1, len(_FakeAsyncInferQueue.instances[0].submissions))
            self.assertFalse(_FakeAsyncInferQueue.instances[0].submissions[0][2])

    def test_cwd_cache_path_cold_warm_and_stale_model_behavior(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            mock.patch.object(Path, "cwd", return_value=Path(temp_dir)),
        ):
            model_path = Path(temp_dir) / "models" / "det.onnx"
            model_path.parent.mkdir()
            model_path.write_bytes(b"model")

            cache_path = _model_cache_path(model_path, "onnxruntime", ".optimized.onnx")
            self.assertEqual(Path(temp_dir) / "cache" / "onnxruntime", cache_path.parent)
            self.assertTrue(cache_path.parent.is_dir())
            cache_path.write_bytes(b"optimized")
            self.assertTrue(_cache_is_current(model_path, cache_path))
            self.assertEqual(
                cache_path,
                _model_cache_path(model_path, "onnxruntime", ".optimized.onnx"),
            )

            model_stat = model_path.stat()
            os.utime(
                model_path,
                ns=(model_stat.st_atime_ns, cache_path.stat().st_mtime_ns + 1_000_000_000),
            )
            self.assertFalse(_cache_is_current(model_path, cache_path))


class DesktopReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = runpy.run_path(str(ROOT / "desktop/scripts/build-shell-release.py"))

    def test_shell_publish_uploads_when_cnb_release_has_no_assets(self):
        publish = self.release["publish"]
        cnb_api = "https://api.cnb.cool/GodRaymond233/ok-bd2-update/-/releases"
        for domestic in (None, {"id": 11, "assets": None}, {"id": 11, "assets": []}):
            with self.subTest(domestic=domestic), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder) / "shell.zip"
                archive.write_bytes(b"shell fixture")
                manifest = {
                    "version": "0.3.6",
                    "source_sha256": "source",
                    "sha256": self.release["file_hash"](archive),
                }
                release = {
                    "assets": [
                        {
                            "name": "shell-build.json",
                            "browser_download_url": "https://example.test/meta",
                        },
                        {
                            "name": archive.name,
                            "browser_download_url": "https://example.test/shell",
                        },
                    ]
                }
                responses = [release, domestic]
                if domestic is None:
                    responses.append({"id": 11, "assets": None})
                confirmation = f"{cnb_api}/11/asset-upload-confirmation/upload"
                responses.extend(
                    [
                        {
                            "upload_url": "https://storage.example.test/upload",
                            "verify_url": confirmation,
                        },
                        {},
                        {
                            "id": 11,
                            "assets": [
                                {
                                    "name": archive.name,
                                    "browser_download_url": "https://example.test/domestic",
                                }
                            ],
                        },
                    ]
                )
                api = Mock(side_effect=responses)
                verify = Mock()
                with (
                    patch.dict(
                        os.environ, GH_TOKEN="fixture", CNB_GH="fixture", GITHUB_REF_NAME="v2.0.0"
                    ),
                    patch.dict(
                        publish.__globals__, {"api": api, "download": Mock(), "verify_urls": verify}
                    ),
                    patch("subprocess.check_output", return_value="v2.0.0\nv1.4.13\n"),
                    patch(
                        "urllib.request.urlopen",
                        side_effect=[io.BytesIO(json.dumps(manifest).encode()), io.BytesIO(b"")],
                    ) as open_url,
                ):
                    self.assertIs(publish(archive, manifest, "owner/repository"), manifest)
                request = open_url.call_args_list[1].args[0]
                self.assertEqual(request.get_method(), "PUT")
                self.assertEqual(request.data, archive.read_bytes())
                self.assertIsNone(request.get_header("Authorization"))
                api.assert_any_call(confirmation, "fixture", {})
                self.assertEqual(
                    manifest["urls"],
                    [
                        "https://example.test/domestic",
                        "https://github.com/owner/repository/releases/download/"
                        "desktop-shell-0.3.6/shell.zip",
                    ],
                )
                verify.assert_called_once_with(manifest)

    def test_electron_payload_prunes_framework_qt_and_refuses_qt_dependencies(self):
        prepare = self.release["prepare_core_payload"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("ok/gui", "ok/ui/qt", "ok/core"):
                path = root / name
                path.mkdir(parents=True)
                (path / "module.py").write_text("", "utf-8")
            requirements = root / "requirements.txt"
            requirements.write_text("fastapi==0.143.0", "utf-8")
            prepare(root)
            self.assertFalse((root / "ok/ui/qt").exists())
            self.assertFalse((root / "ok/gui").exists())
            self.assertTrue((root / "ok/core/module.py").exists())
            requirements.write_text("PySide6==6.10.0", "utf-8")
            with self.assertRaisesRegex(ValueError, "Qt dependencies"):
                prepare(root)

    @unittest.skipUnless(os.name == "nt", "Windows update payload")
    def test_update_payload_carries_ocr_in_its_own_tree(self):
        from src.compat import dependency_guard

        # A source checkout requires its lock even when an OCR wheel is importable.
        with tempfile.TemporaryDirectory() as folder:
            guard_file = str(Path(folder) / "src/compat/dependency_guard.py")
            with (
                patch.object(dependency_guard, "__file__", guard_file),
                self.assertRaisesRegex(dependency_guard.OcrComponentError, "源码依赖锁损坏"),
            ):
                dependency_guard.find_missing_modules()
        inline = self.release["inline_core_requirements"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "data/apps/ok-bd2/working"
            (root / "src").mkdir(parents=True)
            config = root / "src/config.py"
            config.write_text('version = "release-tag-unset"\n', "utf-8")
            requirements = root / "requirements.txt"
            shutil.copyfile(ROOT / "requirements.txt", requirements)

            source_list = (ROOT / "deploy.txt").read_bytes()
            self.assertNotIn(b"\r", source_list)
            self.assertIn(b"ocr_bundle/onnxocr", source_list.split(b"\n"))
            deploy = root / "deploy.txt"
            deploy.write_bytes(b"src\r\nok\r\n")

            stale = root / dependency_guard.OCR_BUNDLE_DIR / "onnxocr/fonts/simfang.ttf"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"previous payload")
            inline(root, "v99.0.1")

            self.assertFalse(stale.exists())
            for entry in ("models/ppocrv5", "ocr_images_pdfs.py"):
                self.assertFalse((stale.parents[1] / entry).exists())
            self.assertEqual('version = "v99.0.1"\n', config.read_text("utf-8"))
            bundle = root / dependency_guard.OCR_BUNDLE_DIR
            ocr = bundle / "onnxocr"
            prepared_list = deploy.read_bytes()
            self.assertNotIn(b"\r", prepared_list)
            self.assertIn(ocr.relative_to(root).as_posix().encode(), prepared_list.split(b"\n"))
            pins = requirements.read_text("utf-8")
            self.assertNotIn("onnxocr-ppocrv6==", pins)
            self.assertIn("openvino==", pins)
            models = ocr / "models/ppocrv6/small"
            for filename in (
                ocr / "LICENSE",
                models / "det/det.onnx",
                models / "rec/rec.onnx",
                models / "ppocrv6_dict.txt",
            ):
                with self.subTest(filename=filename.name):
                    self.assertTrue(filename.is_file())
            probe = (
                "import sys, importlib.util as u; from src.compat import dependency_guard as g; "
                "g.__file__ = sys.argv[1]; assert 'onnxocr' not in g.find_missing_modules(); "
                "print(u.find_spec('onnxocr').origin)"
            )
            command = [sys.executable, "-c", probe, str(root / "src/compat/dependency_guard.py")]
            positive = subprocess.check_output(command, cwd=ROOT, text=True)
            self.assertTrue((ocr / "__init__.py").samefile(positive.strip()), positive)
            # An earlier pip wheel occupies the very same import name.
            ocr.rename(root / "onnxocr")
            env = {**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
            negative = subprocess.run(
                command, cwd=ROOT, capture_output=True, env=env, encoding="utf-8"
            )
            self.assertNotEqual(0, negative.returncode)
            self.assertIn("OCR 组件不完整", negative.stderr)
