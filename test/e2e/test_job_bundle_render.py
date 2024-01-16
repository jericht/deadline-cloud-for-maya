import dataclasses
import glob
import logging
import os
import pathlib
import posixpath
import subprocess
import yaml


from deadline_test_fixtures import (
    BootstrapResources,
    CommandResult,
    DeadlineClient,
    DeadlineResources,
    DeadlineWorkerConfiguration,
    EC2InstanceWorker,
    EC2InstanceWorkerProps,
    Instance,
    InstanceProps,
    Job,
    ServiceModel,
)

import pytest

import boto3

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
_CONFIGURE_MAYA_WORKER_PATH = _REPO_ROOT / "test" / "e2e" / "assets" / "configure-maya-worker.sh"

LOG = logging.getLogger(__name__)

@pytest.fixture(scope="session")
def job_bundles() -> dict[str, pathlib.Path]:
    job_bundle_output_tests_dir = _REPO_ROOT / "job_bundle_output_tests"
    job_bundles: dict[str, pathlib.Path] = {}
    for f in job_bundle_output_tests_dir.iterdir():
        if not f.is_dir():
            continue

        job_bundle_dir = f / "expected_job_bundle"
        if not job_bundle_dir.is_dir():
            continue
        
        job_bundles[f.name] = job_bundle_dir
    
    return job_bundles

@pytest.fixture(scope="session")
def maya_2023_archive() -> pathlib.Path:
    if (path := os.environ.get("MAYA_2023_ARCHIVE_PATH")) is None:
        raise Exception("Maya 2023 is required but was not provided")
    return pathlib.Path(path)

@pytest.fixture(scope="session")
def deadline_cloud_for_maya_whl() -> pathlib.Path:
    if whl_path := os.environ.get("DEADLINE_CLOUD_FOR_MAYA_WHL"):
        return pathlib.Path(whl_path)
    else:
        return _REPO_ROOT / "dist" / "deadline_cloud_for_maya*.whl"

@pytest.fixture(scope="session")
def instance_props(
    maya_2023_archive: pathlib.Path,
    deadline_cloud_for_maya_whl: pathlib.Path,
    service_model: ServiceModel,
) -> InstanceProps:
    resolved_whl_paths = glob.glob(str(deadline_cloud_for_maya_whl))
    assert (
        len(resolved_whl_paths) == 1
    ), f"Expected exactly one deadline-cloud-for-maya whl path, but got {resolved_whl_paths} (from pattern {deadline_cloud_for_maya_whl})"
    resolved_whl_path = resolved_whl_paths[0]

    dest_path = posixpath.join("/tmp", os.path.basename(resolved_whl_path))
    global deadline_cloud_for_maya_whl_path
    deadline_cloud_for_maya_whl_path = dest_path

    return InstanceProps(
        instance_type="t3.micro",
        # instance_type="c5.4xlarge",
        file_mappings={
            str(maya_2023_archive): "/MayaIO2023.run",
            resolved_whl_path: dest_path,
            service_model.file_path: "/tmp/deadline-cloud-service-model.json",
            _CONFIGURE_MAYA_WORKER_PATH: "/tmp/configure-maya-worker.sh",
        },
        bootstrap_bucket_name="deadline-scaffolding-worker-bootstrap-162923712518",
        instance_profile_name="DeadlineScaffoldingWorkerBootstrapInstanceProfile",
        security_group_id="sg-0cfbf4b6c352315ee",
        subnet_id="subnet-0f2a16de29ef9edac",
    )

@pytest.fixture(scope="session")
def instance(instance_props: InstanceProps) -> Instance:
    try:
        instance = Instance(
            instance_props,
            s3_client=boto3.client("s3"),
            ec2_client=boto3.client("ec2"),
            ssm_client=boto3.client("ssm"),
        )
        instance.start()
        yield instance
    finally:
        try:
            instance.stop()
        except Exception as e:
            LOG.error(f"ERROR: Failed to stop instance {instance.instance_id}: {e}")
            raise

@pytest.fixture(scope="session")
def ec2_worker_props(
    instance_props: InstanceProps,
    deadline_client: DeadlineClient,
    worker_config: DeadlineWorkerConfiguration,
) -> EC2InstanceWorkerProps:
    return EC2InstanceWorkerProps(
        subnet_id=instance_props.subnet_id,
        security_group_id=instance_props.security_group_id,
        instance_profile_name=instance_props.instance_profile_name,
        instance_type=instance_props.instance_type,
        bootstrap_bucket_name=instance_props.bootstrap_bucket_name,
        user_data_commands=instance_props.user_data_commands,
        override_ami_id=instance_props.override_ami_id,
        os_user=instance_props.os_user,
        file_mappings=instance_props.file_mappings,
        deadline_client=deadline_client,
        configuration=worker_config,
    )

@pytest.fixture(scope="session")
def ec2_worker(ec2_worker_props: EC2InstanceWorkerProps) -> EC2InstanceWorker:
    try:
        worker = EC2InstanceWorker(
            props=ec2_worker_props,
            s3_client=boto3.client("s3"),
            ec2_client=boto3.client("ec2"),
            ssm_client=boto3.client("ssm"),
        )
        worker.start()
        yield worker
    finally:
        try:
            worker.stop()
        except Exception as e:
            LOG.error(f"ERROR: Failed to stop instance {worker.instance.instance_id}: {e}")
            raise

@pytest.fixture(scope="session", autouse=True)
def install_maya(ec2_worker: EC2InstanceWorker, **kwargs) -> None:
    instance = ec2_worker.instance
    def run_command(cmd: str, **kwargs) -> CommandResult:
        LOG.info(f"About to run: {cmd}")
        cmd_result = instance.send_command(cmd, **kwargs)
        LOG.info(f"stdout:\n{cmd_result.stdout}\nstderr:\n{cmd_result.stderr}\n")
        if cmd_result.exit_code != 0:
            raise Exception(f"Command failed: {cmd_result}")
        return cmd_result

    run_command(
        " && ".join([
            "chmod +x /tmp/configure-maya-worker.sh",
            f"export INSTANCE_ID={instance.instance_id}",
            f"export WORKER_USER={ec2_worker.props.configuration.user}",
            f"export JOB_USER={ec2_worker.configuration.job_users[0].user}",
            f"export MAYA_ADAPTOR_WHL_PATH={deadline_cloud_for_maya_whl_path}",
            "/tmp/configure-maya-worker.sh",
        ]),
        wait_delay=10,
        wait_max_attempts=200,
    )

    # # Wait for UserData execution to complete
    # run_command("cloud-init status --wait")
    # run_command(f"cat /var/lib/cloud/instances/{instance.instance_id}/cloud-init-output.txt || cat /var/log/cloud-init-output.log")

    # # Install MayaIO
    # run_command("sudo chmod +x /MayaIO2023.run")
    # run_command(
    #     "sudo /MayaIO2023.run --nox11 --phase2 -- localhost 2>&1 >/MayaIO2023.log && cat /MayaIO2023.log || cat /MayaIO2023.log",
    # )

    # # Install some system deps, see https://github.com/glpi-project/glpi-agent/issues/391
    # run_command("sudo dnf install -y libxcrypt-compat python3-pip")
    # # Downgrade libffi otherwise MayaAdaptor doesn't work (AL2023 defaults to libffi.so.8, we need 6)
    # run_command("sudo dnf install -y libffi-3.1-28.amzn2023.0.2")
    # # Install ImageMagic for image comparison
    # run_command("sudo dnf install -y ImageMagick")

    # # Configure worker to use jobuser
    # job_user = ec2_worker.props.configuration.job_users[0].user
    # run_command(f"sudo sed -iE 's/# posix_job_user = \"user:group\"/posix_job_user = \"{job_user}:{job_user}\"/' /etc/amazon/deadline/worker.toml")
    # run_command("sudo grep posix_job_user /etc/amazon/deadline/worker.toml")

    # # Setup job user with Maya adaptor
    # # os_user = ec2_worker.props.configuration.user
    # os_user = job_user
    # run_command(f"sudo -iu {os_user} aws codeartifact login --tool pip --domain bealine-client-software-mirror --domain-owner 938076848303 --repository bealine-client-software-mirror")
    # # run_command(f"sudo -iu {os_user} python3 -m venv /home/{os_user}/.venv")
    # run_command(f"runuser --login {os_user} --command 'python3 -m venv $HOME/.venv && echo \". $HOME/.venv/bin/activate\" >> $HOME/.bashrc'")
    # run_command(f"sudo su {os_user} && . /home/{os_user}/.venv/bin/activate && pip install {deadline_cloud_for_maya_whl_path}")
    # # run_command(f"sudo pip install {deadline_cloud_for_maya_whl_path} --prefix /home/deadline-worker/.venv")
    # run_command(f"sudo -iu {os_user} MayaAdaptor --help")

@pytest.mark.usefixtures("ec2_worker")
class TestJobBundleSubmissions:
    def test_job_bundle_output_test_submissions(
        self,
        deadline_client: DeadlineClient,
        deadline_resources: DeadlineResources,
        job_bundles: dict[str, pathlib.Path],
    ) -> None:
        # TODO: parametrize test on fixture value
        for _, job_bundle in job_bundles.items():
            # GIVEN
            template: dict
            with open(str(job_bundle / "template.yaml")) as f:
                template = yaml.safe_load(f)

            # WHEN
            output = subprocess.check_output(
                args=(
                    "deadline",
                    "bundle",
                    "submit",
                    str(job_bundle),
                    "--farm-id", deadline_resources.farm.id,
                    "--queue-id", deadline_resources.queue.id,
                    "--yes",
                ),
                text=True,
            )
            job_id = output.rstrip().splitlines()[-1]
            job = Job(
                farm=deadline_resources.farm,
                queue=deadline_resources.queue,
                template=template,
                **Job.get_job_details(
                    client=deadline_client,
                    farm=deadline_resources.farm,
                    queue=deadline_resources.queue,
                    job_id=job_id,
                ),
            )
            job.wait_until_complete(client=deadline_client)

            # THEN
            job_logs = job.get_logs(deadline_client=deadline_client, logs_client=boto3.client("logs"))
            job_logs_str = "\n".join([log.message for logs in job_logs.logs.values() for log in logs])
            raise Exception(job_logs_str)














# class TestInstance:
#     def test_instance(
#         self,
#         instance: Instance,
#         install_maya: None,
#     ) -> None:
#         # WHEN
#         cmd_result = instance.send_command("whoami")

#         # THEN
#         assert "ec2-user" in cmd_result.stdout

# @pytest.fixture(scope="session")
# def worker_config(
#     worker_config: DeadlineWorkerConfiguration,
#     maya_2023_archive: pathlib.Path,
#     deadline_cloud_for_maya_whl: pathlib.Path,
# ) -> DeadlineWorkerConfiguration:
#     resolved_whl_paths = glob.glob(str(deadline_cloud_for_maya_whl))
#     assert (
#         len(resolved_whl_paths) == 1
#     ), f"Expected exactly one deadline-cloud-for-maya whl path, but got {resolved_whl_paths} (from pattern {deadline_cloud_for_maya_whl})"
#     resolved_whl_path = resolved_whl_paths[0]

#     dest_path = posixpath.join("/tmp", os.path.basename(resolved_whl_path))
#     global deadline_cloud_for_maya_whl_path
#     deadline_cloud_for_maya_whl_path = dest_path
#     return dataclasses.replace(
#         worker_config,
#         # TODO: Failing to configure worker agent. Probably due to the transfer of Maya, need to increase EC2 disk capacity.
#         file_mappings=[
#             *(worker_config.file_mappings or []),
#             # (str(maya_2023_archive), "/Maya2023.tgz"),
#             (str(maya_2023_archive), "/MayaIO2023.run"),
#             # (str(maya_2023_archive), "/MayaIO2023.tgz"),
#             (resolved_whl_path, dest_path),
#         ],
#     )

# @pytest.fixture(scope="session", autouse=True)
# def worker_setup(worker: DeadlineWorker) -> None:
#     def run_command(cmd: str) -> CommandResult:
#         print(f"About to run: {cmd}")
#         cmd_result = worker.send_command(cmd)
#         print(f"stdout:\n{cmd_result.stdout}\nstderr:\n{cmd_result.stderr}\n")
#         if cmd_result.exit_code != 0:
#             raise Exception(f"Command failed: {cmd_result}")
#         return cmd_result

#     run_command("df -H")
#     run_command("cloud-init status --wait")
#     # run_command("aws s3 cp s3://deadline-scaffolding-worker-bootstrap-162923712518/worker/Autodesk_Maya_2023_ML_Linux_64bit.tgz /Maya2023.tgz")
#     # run_command("ls -l /")
#     # run_command(f"mkdir -p /Maya2023 && tar -xf /Maya2023.tgz -C /Maya2023")
#     # run_command(f"mkdir -p /MayaIO2023 && tar -xvf /MayaIO2023.tgz -C /MayaIO2023")
#     # run_command(f"")
#     run_command("ls -l /")
#     # Install Maya deps
#     # run_command("sudo yum install -y libgtk-3-0")
#     # run_command("sudo /Maya2023/Setup --silent")
#     # run_command("sudo chmod +x /MayaIO2023.run && sudo /MayaIO2023.run")
#     # run_command("yes | sudo /MayaIO2023/setup.sh")
#     run_command("sudo chmod +x /MayaIO2023.run")
#     run_command("sudo /MayaIO2023.run --help")
#     run_command("sudo /MayaIO2023.run --target /maya --nox11 --phase2 -- localhost")
#     run_command("sudo rm -rf /maya")
#     run_command("ls -lR /usr/autodesk")
#     run_command("/usr/autodesk/maya2023/bin/mayapy --version")
#     run_command(f"/usr/autodesk/maya2023/bin/mayapy -m pip install {deadline_cloud_for_maya_whl_path}")