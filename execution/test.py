"""
Classes related to test description and execution
"""

import json
import threading
from os import path
import pexpect
from pexpect import pxssh
from pprint import pformat
from execution.retry import retry
from execution.control import wait, stopping
from execution.log import with_logger, setup_logger
from execution.exceptions import OperationFailed, ExitEarly


class TestReplica:
    def __init__(self, test_id, options, experiment, profile, matrix_ids=None, config={}):
        self._id = test_id
        self._options = options
        self._experiment = experiment
        self._profile = profile
        self._matrix_ids = matrix_ids
        self._config = config

    def config(self):
        return self._config

    def id(self):  # pylint: disable=invalid-name
        return self._id

    def options(self):
        return self._options

    def experiment(self):
        return self._experiment

    def profile(self):
        return self._profile

    def matrix_ids(self):
        return self._matrix_ids

    def __repr__(self):
        lines = []
        lines.append(f"experiment: {self._experiment}")
        lines.append(f"profile: {self._profile}")
        if self._matrix_ids is not None:
            lines.append(f"matrix: {pformat(self._matrix_ids)}")
        lines.append(f"options: {json.dumps(self._options)}")
        lines.append(f"config: {json.dumps(self._config)}")
        separator = "\n  "
        return f"Experiment replica ({self._id}):{separator}{separator.join(lines)}"


@with_logger
class TestExecutionThread(threading.Thread):
    def __init__(self, test, hostname, config_path, results_path, log_path,
                 config, experiment, cloudlab, cloudlab_lock):
        threading.Thread.__init__(self)
        self._test = test
        self._hostname = hostname
        self._config_path = config_path
        self._results_path = results_path
        # Merge the test config & the global config
        self._config = {**config, **test.config()}
        self._experiment = experiment
        self._remote_experiment_path = None
        self._cloudlab_driver = cloudlab
        self._cloudlab_lock = cloudlab_lock
        self.logger = setup_logger(inner=self.logger, logfile=log_path, name=f"{self._test.id()}-f",
                                   disableStderrLogger=True, colors=False, indent=False)

    def transfer(self, local_src=None, local_dest=None, remote_path=None, retry_count=1):
        cert_path = self._config.get("ssh_cert", "id_rsa")
        username = self._config.get("username", "root")
        retry_delay = self._config.get("retry_delay", 120)

        prelude = ['-o', 'UserKnownHostsFile=/dev/null',
                   '-o', 'StrictHostKeyChecking=no',
                   '-i', cert_path]
        hoststring = f"{username}@{self._hostname}:{remote_path}"

        to_remote = False
        if local_src is None:
            # Transfer from remote
            args = [*prelude, hoststring, local_dest]
        else:
            # Transfer to remote
            to_remote = True
            args = [*prelude, local_src, hoststring]

        transfer_local = local_src if to_remote else local_dest
        transfer_text = f"'{transfer_local}' {'to' if to_remote else 'from'}"
        self.debug(
            "Transferring file %s %s with options %s; -i %s; retry_delay=%f, retry_count=%d",
            transfer_text, hoststring, json.dumps(prelude), cert_path, retry_delay, retry_count)

        task_messages = (f"transfer {transfer_text} host {self._hostname}",
                         f"transfer {transfer_text} remote")
        for current in retry(retry_count, task=task_messages, logger=self.logger):  # pylint: disable=unexpected-keyword-arg
            child = pexpect.spawn(command="scp", args=args)
            child.expect(pexpect.EOF)
            child.close()

            if child.exitstatus == 0:
                # Successful
                return True
            else:
                current.failed(f"exit code ({child.exitstatus})")

    def terminal(self, retry_count=1):
        cert_path = self._config.get("ssh_cert", "id_rsa")
        username = self._config.get("username", "root")
        server = self._hostname
        options = dict(StrictHostKeyChecking="no",
                       UserKnownHostsFile="/dev/null")
        ssh = pxssh.pxssh(options=options)
        options_text = f"-i {cert_path}; retry_count={retry_count}"
        self.debug("SSHing into %s@%s with options %s; %s",
                   username, server, json.dumps(options), options_text)

        # Capture failed exceptions to close resources
        try:
            task_messages = (f"attach ssh terminal to host {self._hostname}",
                             f"attach ssh terminal to remote")
            for current in retry(retry_count, task=task_messages, logger=self.logger):  # pylint: disable=unexpected-keyword-arg
                try:
                    ssh.login(server, username=username, ssh_key=cert_path)
                except pxssh.ExceptionPxssh as ex:
                    current.failed(ex)
                else:
                    return ssh
        # Close ssh connection before re-throwing exception
        except:
            ssh.close()
            raise

    def run_sequence(self, ssh, sequence, retry_count=1, timeout=30):
        self.debug("Executing sequence %s with options retry_count=%d", sequence, retry_count)

        # Capture failed exceptions to close resources
        try:
            task_messages = (f"execute command sequence to host {self._hostname}",
                             f"execute command sequence to remote")
            for current in retry(retry_count, task=task_messages, logger=self.logger):  # pylint: disable=unexpected-keyword-arg
                failed = False
                for command in sequence:
                    ssh.sendline(command)
                    output = ""
                    while not ssh.prompt(timeout=timeout):
                        output += ssh.before.decode()
                        ssh.sendcontrol('c')
                    output += ssh.before.decode()

                    ssh.sendline("echo $?")
                    ssh.prompt(timeout=10)

                    result = ssh.before.decode().strip().splitlines()
                    if len(result) > 0:
                        try:
                            exitcode = int(result[-1])
                        except TypeError:
                            self.warning("Couldn't decode exit code from command %s: %s",
                                         command, result)
                            exitcode = 1
                    self.debug("[%s] %s", str(exitcode), output)

                    # If exit code is non-zero, assume failed
                    if exitcode != 0:
                        current.failed(f"command: {command}")
                        failed = True
                        break
                if failed:
                    continue
                else:
                    return

        # Close ssh connection before re-throwing exception
        except:
            ssh.close()
            raise

    def run(self):
        try:
            self.info("Starting execution thread (%s)", self._hostname, external=True)
            self.info("Beginning setup")
            self.setup()
            self.info("Finishing setup")
            self.info("Beginning execute")
            self.execute()
            self.info("Finishing execute")
            self.info("Beginning teardown")
            self.teardown()
            self.info("Finishing teardown")
            self.info("Exiting execution thread (%s)", self._hostname, external=True)
        except ExitEarly:
            self.warning("Exiting test early")
        except OperationFailed:
            self.error("Failed test; exiting")

    def setup(self):
        # Transfer SSH certificate
        cert_path = self._config.get("ssh_cert", "id_rsa")
        self.info("Transfering the SSH certificate from %s to remote:.ssh/id_rsa", cert_path)
        self.transfer(local_src=cert_path, remote_path=".ssh/id_rsa", retry_count=10)

        # Transfer rendered config file
        remote_config = self._config.get("remote_config", "config.sh")
        self.info("Transfering rendered config file from %s to remote:%s",
                  self._config_path, remote_config)
        self.transfer(local_src=self._config_path, remote_path=remote_config, retry_count=10)

    def execute(self):
        for current in retry(retry_count=5, task=f'executing experiment {self._test.id()}', logger=self.logger):
            try:
                # Attach a remote terminal to the executor host
                self.info("Attaching a remote terminal to the executor host")
                ssh = self.terminal(retry_count=10)

                # Clone the repo
                repo = self._config.get("repo")
                remote_folder = "repo"
                self.info("Cloning the repo %s into remote:%s", repo, remote_folder)

                # Build the git command with optional branch support
                git_command = ["git", "clone"]
                branch = self._config.get("branch", None)
                if branch:
                    git_command.extend(["--single-branch", "--branch", f'"{branch}"'])
                git_command.extend([f'"{repo}"', remote_folder])

                clone_sequence = [f'sudo rm -rf {remote_folder}',
                                  ' '.join(git_command)]
                self.run_sequence(ssh, sequence=clone_sequence, retry_count=10, timeout=120)

                # Copy the config file into place
                experiments_path = self._config.get("experiments_path", "experiments")
                self._remote_experiment_path = path.join(
                    remote_folder,
                    experiments_path,
                    self._test.experiment())
                remote_config = self._config.get("remote_config", "config.sh")
                dest_config_path = path.join(
                    self._remote_experiment_path,
                    "conf",
                    remote_config)
                self.debug("Copy the config file from remote:%s into place at remote:%s",
                           remote_config, dest_config_path)
                self.run_sequence(
                    ssh, sequence=[f'cp {remote_config} {dest_config_path}'], retry_count=10)

                # Change the working directory to the experiment root (eventual destination of results)
                self.debug("Change the working directory to remote:%s",
                           self._remote_experiment_path)
                self.run_sequence(
                    ssh, sequence=[f'cd {self._remote_experiment_path}'], retry_count=1)

                # Run the primary script
                script_path = './scripts/run.sh'
                self.info("Running primary script at remote:%s", script_path)
                ssh.sendline(script_path)
                while not ssh.prompt(timeout=60):
                    self.debug("\n%s", ssh.before.decode().strip())
                    if ssh.before:
                        ssh.expect(r'.+')
                self.debug("\n%s", ssh.before.decode().strip())
                self.info("Finished primary script")
                ssh.logout()
                return
            except ExitEarly:
                raise
            except Exception as ex:
                current.failed(ex)

    def teardown(self):
        # Move the results tar to /results/{id}.tar.gz
        results_path = path.join(self._remote_experiment_path, "results.tar.gz")
        self.debug("Moving the results tar from remote:%s to %s", results_path, self._results_path)
        self.transfer(remote_path=results_path, local_dest=self._results_path, retry_count=5)

        # Check for stopping before attempting to acquire mutex (might be poisoned)
        if stopping:
            return

        # Terminate the experiment on cloudlab
        with self._cloudlab_lock:
            try:
                old_logger = self._cloudlab_driver.logger
                self._cloudlab_driver.set_logger(self.logger)
                self._cloudlab_driver.terminate(self._experiment)
            except OperationFailed as ex:
                self.error("Could not terminate experiment on cloudlab:")
                self.error(ex)
            except Exception as ex:
                self.error("Encountered error while terminating experiment on cloudlab driver:")
                self.error(ex)
            finally:
                self._cloudlab_driver.set_logger(old_logger)

        # Sleep for 5 minutes between teardown and provisioning
        backoff_dur = 5
        self.info("Sleeping for %d minutes after finished experiment", backoff_dur)
        if wait(backoff_dur * 60):
            raise ExitEarly()
