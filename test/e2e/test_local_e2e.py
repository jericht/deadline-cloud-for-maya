# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

import dataclasses
import os
import pathlib
import subprocess

import pytest

class TestJobBundleOutputTestScenes:
    @pytest.fixture
    def test_bundle_paths(self) -> dict[str, pathlib.Path]:
        """Gets a mapping of test name to bundle path from Job Bundle Output test dir structure"""
        job_bundle_tests_dir = pathlib.Path(os.environ["JOB_BUNDLE_TESTS_DIR"])

        test_bundle_paths: dict[str, pathlib.Path] = {}
        for f in job_bundle_tests_dir.iterdir():
            if not f.is_dir():
                continue

            job_bundle_path = f / "expected_job_bundle"
            if not os.path.isdir(job_bundle_path):
                continue

            test_bundle_paths[f.name] = job_bundle_path

        return test_bundle_paths

    def test_local_maya_render(self, test_bundle_paths: dict[str, pathlib.Path]) -> None:
        # output = subprocess.check_output(("openjd", "--help"))
        # raise Exception(f"Hi: {output}")

        # GIVEN
        for name, job_bundle_path in test_bundle_paths.items():
            # WHEN
            # proc = subprocess.Popen(("openjd", "run", str(job_bundle_path)))
            proc = subprocess.Popen(
                args=("openjd", "run", str(job_bundle_path)),
                text=True,
            )

            # THEN
            proc.wait(5)

# TODO: TEMPORARY UNTIL JOB BUNDLES SUPPORTED BY OPENJD
# Stopping for now to do Deadline Cloud mode POC since OpenJD CLI needs work for this
def submit_job_bundle_to_openjd(job_bundle_path: pathlib.Path) -> subprocess.Popen:
    return subprocess.Popen(
        args=("openjd", "run", str(job_bundle_path)),
        text=True,
    )