"""QuantCoreJSONResponse: a NaN/inf anywhere in a payload is null, never a 500.

`/options/iv-rank` 500'd for WDC with "Out of range float values are not JSON
compliant: nan": the encoder's `default()` nulls NaN, but json never calls
`default()` for a plain float, and np.float64 is a float.
"""
import json
import unittest

import numpy as np

from api.json_response import QuantCoreJSONResponse


def body(content):
    return json.loads(QuantCoreJSONResponse(content).body)


class NonFiniteFloatsTest(unittest.TestCase):
    def test_plain_and_numpy_non_finite_floats_become_null(self):
        out = body({
            "nan": float("nan"),
            "inf": float("inf"),
            "np_nan": np.float64("nan"),
            "nested": {"rows": [{"iv": float("-inf")}, {"iv": 41.5}], "pair": (float("nan"), 2)},
            "ok": 1.25,
        })
        self.assertEqual(out, {
            "nan": None, "inf": None, "np_nan": None,
            "nested": {"rows": [{"iv": None}, {"iv": 41.5}], "pair": [None, 2]},
            "ok": 1.25,
        })

    def test_clean_payload_is_unchanged(self):
        content = {"a": [1, 2.5, "x", None], "b": {"c": True}}
        self.assertEqual(body(content), content)

    def test_other_value_errors_still_raise(self):
        # Only the non-finite case is rescued: a circular payload still fails.
        loop = []
        loop.append(loop)
        with self.assertRaises(ValueError):
            QuantCoreJSONResponse({"loop": loop})


if __name__ == "__main__":
    unittest.main()
