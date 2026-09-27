#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_secrets_example.py
# Description: The bundle ships IndigoSecrets_example.py, and it names every key the
#              plugin reads from IndigoSecrets.py, each with an empty value. Until
#              1.19 there was no example at all, so a user told to "set DAHUA_USER /
#              DAHUA_PASS in IndigoSecrets.py" had nothing to copy. Read by AST, so a
#              key added to plugin.py and forgotten here fails the suite.
# Author:      CliveS & Claude Opus 5.5
# Date:        27-09-2026
# Version:     1.0

import ast
import os
import unittest

HERE   = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(os.path.dirname(HERE), "DahuaEvents.indigoPlugin", "Contents",
                      "Server Plugin")
PLUGIN  = os.path.join(BUNDLE, "plugin.py")
EXAMPLE = os.path.join(BUNDLE, "IndigoSecrets_example.py")


def _tree(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _keys_the_plugin_reads():
    return {alias.name for node in ast.walk(_tree(PLUGIN))
            if isinstance(node, ast.ImportFrom) and node.module == "IndigoSecrets"
            for alias in node.names}


def _example_assignments():
    out = {}
    for node in _tree(EXAMPLE).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


class TestSecretsExample(unittest.TestCase):

    def test_the_example_ships_in_the_bundle(self):
        self.assertTrue(os.path.isfile(EXAMPLE), "IndigoSecrets_example.py is missing")

    def test_the_plugin_reads_at_least_one_key(self):
        # Without this the next test would pass over an empty set.
        self.assertIn("DAHUA_USER", _keys_the_plugin_reads())

    def test_every_key_the_plugin_reads_is_in_the_example(self):
        missing = _keys_the_plugin_reads() - set(_example_assignments())
        self.assertFalse(missing, f"not in IndigoSecrets_example.py: {sorted(missing)}")

    def test_every_value_in_the_example_is_empty(self):
        for name, value in _example_assignments().items():
            with self.subTest(key=name):
                self.assertIsInstance(value, ast.Constant)
                self.assertEqual(value.value, "", f"{name} carries a value")


if __name__ == "__main__":
    unittest.main()
