import importlib.metadata
import importlib.util
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from onnxocr.predict_base import PredictBase, _cache_is_current, _model_cache_path
from onnxocr.utils import infer_args

ROOT = Path(__file__).resolve().parents[1]


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

    def set_property(self, properties):
        self.__class__.cache_properties.append(properties)

    @staticmethod
    def read_model(*, model):
        return _FakeModel()

    @staticmethod
    def compile_model(*, model, device_name):
        return _FakeCompiledModel()


def _fake_openvino_module():
    return types.SimpleNamespace(Core=_FakeCore, AsyncInferQueue=_FakeAsyncInferQueue)


class OnnxOcrRuntimeTest(unittest.TestCase):
    def test_onnxocr_version_and_async_parser_contract(self):
        self.assertEqual("0.0.22", importlib.metadata.version("onnxocr-ppocrv5"))
        parser = infer_args()
        self.assertEqual(1, parser.parse_args([]).openvino_num_requests)
        self.assertEqual(
            3, parser.parse_args(["--openvino_num_requests", "3"]).openvino_num_requests
        )

    def test_installed_predict_base_does_not_enumerate_available_devices(self):
        # onnxocr 0.0.20 called core.available_devices during init, which also
        # initializes the OpenVINO GPU plugin and crashed with 0xc0000005 on
        # some GPU driver stacks (BUG-20260829-03); 0.0.22 probes NPU lazily.
        spec = importlib.util.find_spec("onnxocr.predict_base")
        self.assertIsNotNone(spec)
        source = Path(spec.origin).read_text(encoding="utf-8")
        # 0.0.22 keeps the phrase inside an explanatory comment only.
        self.assertNotIn("devices = core.available_devices", source)
        self.assertIn('core.get_property("NPU", "AVAILABLE_DEVICES")', source)

    def test_async_infer_queue_default_and_explicit_jobs_are_bounded(self):
        _FakeAsyncInferQueue.instances.clear()
        _FakeCore.cache_properties.clear()
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
            )

            self.assertEqual([1, 2], [queue.jobs for queue in _FakeAsyncInferQueue.instances])
            self.assertEqual(2, len(explicit._async_queue))
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
            os.utime(model_path, ns=(model_stat.st_atime_ns, model_stat.st_mtime_ns + 10_000_000))
            self.assertFalse(_cache_is_current(model_path, cache_path))

    def test_root_cache_ignore_is_anchored_without_hiding_business_cache(self):
        lines = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("/cache/", lines)
        self.assertNotIn("cache/", lines)
        self.assertNotIn("**/cache/", lines)

    def test_ok_script_190_does_not_forward_unknown_onnxocr_params(self):
        self.assertEqual("1.0.190", importlib.metadata.version("ok-script"))
        spec = importlib.util.find_spec("ok.task.TaskExecutor")
        self.assertIsNotNone(spec)
        source = Path(spec.origin).read_text(encoding="utf-8")
        start = source.index("elif lib == 'onnxocr':")
        end = source.index("elif lib == 'rapidocr':", start)
        onnxocr_factory = source[start:end]
        self.assertIn("use_npu", onnxocr_factory)
        self.assertIn("use_openvino", onnxocr_factory)
        self.assertNotIn("openvino_num_requests", onnxocr_factory)

        config_text = (ROOT / "src" / "config.py").read_text(encoding="utf-8")
        self.assertIn('"use_openvino": True', config_text)
        self.assertNotIn('"openvino_num_requests"', config_text)


if __name__ == "__main__":
    unittest.main()
