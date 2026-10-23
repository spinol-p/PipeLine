import unittest
import os
from core.versions import load_env_versions, get_docker_image


class TestVersions(unittest.TestCase):
    def test_load_env_versions(self):
        versions = load_env_versions()
        self.assertIn("IMG_GITLEAKS", versions)
        self.assertIn("IMG_SEMGREP", versions)
        self.assertIn("IMG_SCHEMATHESIS", versions)
        self.assertEqual(versions["IMG_SCHEMATHESIS"], "schemathesis/schemathesis:3.39.8")

    def test_get_docker_image(self):
        img = get_docker_image("IMG_GITLEAKS", "fallback:latest")
        self.assertEqual(img, "zricethezav/gitleaks:v8.21.2")

        fallback = get_docker_image("NON_EXISTENT_VAR", "fallback:latest")
        self.assertEqual(fallback, "fallback:latest")


if __name__ == "__main__":
    unittest.main()
