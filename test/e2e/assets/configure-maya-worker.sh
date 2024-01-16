#!/bin/bash

INSTANCE_ID=${INSTANCE_ID:?}
JOB_USER=${JOB_USER:?}
WORKER_USER=${WORKER_USER:?}
MAYA_ADAPTOR_WHL_PATH=${MAYA_ADAPTOR_WHL_PATH:?}

cloud-init status --wait
cat /var/lib/cloud/instances/$INSTANCE_ID/cloud-init-output.txt || cat /var/log/cloud-init-output.log

# Install MayaIO
sudo chmod +x /MayaIO2023.run
sudo /MayaIO2023.run --nox11 --phase2 -- localhost

# Install some system deps, see https://github.com/glpi-project/glpi-agent/issues/391
sudo dnf install -y libxcrypt-compat python3-pip

# Downgrade libffi otherwise MayaAdaptor doesn't work (AL2023 defaults to libffi.so.8, we need 6)
sudo dnf install -y libffi-3.1-28.amzn2023.0.2

# Install ImageMagic for image comparison
sudo dnf install -y ImageMagick

# Configure worker to use job user
sudo sed -iE 's/# posix_job_user = "user:group"/posix_job_user = "'"$JOB_USER:$JOB_USER"'"/' /etc/amazon/deadline/worker.toml
sudo grep posix_job_user /etc/amazon/deadline/worker.toml

# Setup job user with Maya adaptor
# sudo -iu $JOB_USER aws codeartifact login --tool pip --domain bealine-client-software-mirror --domain-owner 938076848303 --repository bealine-client-software-mirror
runuser --login $JOB_USER --command 'aws codeartifact login --tool pip --domain bealine-client-software-mirror --domain-owner 938076848303 --repository bealine-client-software-mirror'
runuser --login $JOB_USER --command 'python3 -m venv $HOME/.venv && echo ". $HOME/.venv/bin/activate" >> $HOME/.bashrc'
# sudo su $JOB_USER && . /home/$JOB_USER/.venv/bin/activate && pip install $MAYA_ADAPTOR_WHL_PATH1
runuser --login $JOB_USER --command "pip install $MAYA_ADAPTOR_WHL_PATH"
# sudo -iu $JOB_USER MayaAdaptor --help
runuser --login $JOB_USER --command 'MayaAdaptor --help'