#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_worker_codes.py
# Description: A camera's second device must reach the camera's stream. The stream's
#              event codes are fixed when it opens, and devices start one at a time,
#              so until 1.18 a camera's Vehicle device never got its code and never
#              switched on. These tests pin the rule that replaces a running worker
#              whose codes no longer cover the camera's devices.
# Author:      CliveS & Claude Opus 5.5
# Date:        27-09-2026
# Version:     1.0

import ast
import os
import unittest

HERE   = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "DahuaEvents.indigoPlugin", "Contents",
                      "Server Plugin", "plugin.py")

with open(PLUGIN, encoding="utf-8") as fh:
    TREE = ast.parse(fh.read())


def _load_function(name):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), PLUGIN, "exec"), namespace)
    return namespace[name]


def _method(class_name, name):
    klass = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    return next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)


worker_is_current = _load_function("worker_is_current")


class _Worker:
    def __init__(self, codes, alive=True):
        self.codes = set(codes)
        self._alive = alive

    def is_alive(self):
        return self._alive


class WorkerIsCurrent(unittest.TestCase):
    def test_no_worker_is_not_current(self):
        self.assertFalse(worker_is_current(None, {"SmartMotionHuman"}))

    def test_a_dead_worker_is_not_current(self):
        self.assertFalse(worker_is_current(_Worker({"SmartMotionHuman"}, alive=False),
                                           {"SmartMotionHuman"}))

    def test_the_second_device_on_a_camera_replaces_the_stream(self):
        # Person started first and opened the stream for its code alone.
        first = _Worker({"SmartMotionHuman"})
        self.assertFalse(worker_is_current(first, {"SmartMotionHuman", "SmartMotionVehicle"}))

    def test_a_worker_asking_for_every_code_is_kept(self):
        both = {"SmartMotionHuman", "SmartMotionVehicle"}
        self.assertTrue(worker_is_current(_Worker(both), set(both)))


class EnsureWorkerUsesTheRule(unittest.TestCase):
    """Walk what _ensure_worker executes, not its text."""

    def setUp(self):
        self.fn = _method("Plugin", "_ensure_worker")

    def test_it_asks_whether_the_running_worker_is_current(self):
        calls = [n.func.id for n in ast.walk(self.fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertIn("worker_is_current", calls)

    def test_it_stops_a_stale_worker_before_starting_another(self):
        attrs = [n.func.attr for n in ast.walk(self.fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertIn("_stop_worker", attrs)

    def test_the_new_worker_gets_the_codes_it_was_judged_against(self):
        worker_call = next(n for n in ast.walk(self.fn)
                           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                           and n.func.id == "CameraWorker")
        codes = next(k.value for k in worker_call.keywords if k.arg == "codes")
        self.assertIsInstance(codes, ast.Name)
        self.assertEqual(codes.id, "codes")


if __name__ == "__main__":
    unittest.main()
