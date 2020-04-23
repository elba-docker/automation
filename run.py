#!/usr/bin/env python3

"""
Automation script for running cloudlab experiments
"""

import re
import sys
import os
import copy
import traceback
import getpass
import signal
import threading
from os import path
from pathlib import Path
from typing import *
import click
import yaml
from deepmerge import always_merger
from execution.retry import retry
from execution.cloudlab import Cloudlab
from execution.log import log, setup_logger
from execution.control import stop, stopping
from execution.test import TestExecutionThread, TestReplica
from execution.exceptions import OperationFailed, ExitEarly


HOST_CONFIG_REGEX = re.compile(r'(?m)^((?:readonly )?[A-Z_]+_HOSTS?)="?.*"?$')
RESULT_TAR_REGEX = re.compile(r'(?:^|.*/{1,2})(.+)-(\d+)\.tar\.gz$')
thread_queue = []  # pylint: disable=invalid-name
cloudlab = None  # pylint: disable=invalid-name
cloudlab_lock = threading.Lock()  # pylint: disable=invalid-name


@click.command()
@click.option("--config", "-c", prompt="Automation config YAML file")
@click.option("--repo_path", "-r", prompt="Path to locally cloned repo")
@click.option("--cert", "-C", prompt="Path to private SSL certificate",
              default="~/.ssh/id_rsa", required=False)
@click.option("--threads", "-t", prompt="Maximum concurrency for running experiments",
              default=1, required=False)
@click.option("--password", "-p", prompt="Path to file containing password",
              default=None, required=False)
@click.option("--headless/--no-headless", prompt="Run chrome driver in headless mode",
              default=False)
def main(config: str = None, repo_path: str = None, cert: str = None, threads: str = None, password:
         str = None, headless: str = False) -> None:
    if config is None:
        return

    config_dict = load_config(config)
    if config_dict is None:
        return

    if "ssh_cert" not in config_dict:
        config_dict["ssh_cert"] = cert

    if "max_concurrency" not in config_dict:
        config_dict["max_concurrency"] = threads

    if "password_path" not in config_dict:
        config_dict["password_path"] = password

    if "headless" not in config_dict:
        config_dict["headless"] = headless

    run(config_dict, repo_path)


def run(config: Dict[str, Any], repo_path: str) -> None:
    log.info("Starting automated experiment execution")
    if "tests" not in config or not config["tests"]:
        log.error("No tests found. Exiting")
        return

    if "repo" not in config:
        log.error("No repo found. Exiting")
        return

    # Make local directories
    Path("working").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    Path("results").mkdir(exist_ok=True)

    # Check for existence of experiments directory
    experiments_dir = path.join(repo_path, config.get("experiments_path", "."))
    if not path.exists(experiments_dir):
        log.error("Experiment directory %s not found", experiments_dir)
        return

    tests = flatten_tests(config)

    # Initialize cloudlab driver
    username = config.get("username")
    if username is None:
        log.error("Cloudlab experiment username not specified")
        return

    # Load Cloudlab password
    if 'password_path' in config:
        password_path = config['password_path']
        try:
            with open(password_path, 'r') as password_file:
                password = password_file.read().strip()
        except IOError as ex:
            log.error("Could not load Cloudlab password file at %s:", password_path)
            log.error(ex)
            return
    else:
        password = getpass.getpass(prompt=f'Cloudlab password for {username}: ')

    # Instantiate the driver
    headless = bool(config.get("headless"))
    global cloudlab  # pylint: disable=global-statement, invalid-name
    log.info("Initializing %s cloudlab driver for %s",
             'headless' if headless else 'gui', username)
    cloudlab = Cloudlab(username, password, headless)

    # Attempt to log in
    with cloudlab_lock:
        try:
            log.info("Logging into cloudlab")
            cloudlab.login()
        except ExitEarly:
            return
        except OperationFailed as ex:
            log.error("Could not log into cloudlab:")
            log.error(ex)
            log.error(traceback.format_exc())
            return
        except Exception as ex:
            log.error("Encountered error while logging into cloudlab driver:")
            log.error(ex)
            log.error(traceback.format_exc())
            return
        else:
            log.info("Cloudlab login successful")

    max_concurrency = config.get("max_concurrency", 1)

    for test in tests:
        test_logger = setup_logger(name=test.id(), inner=log, prefix=f"[{test.id()}] ")
        try:
            for current in retry(task=f"executing test {test.id()}", retry_count=5, logger=test_logger):  # pylint: disable=unexpected-keyword-arg
                # Make sure there aren't more than `max_concurrency` tests executing
                while len(thread_queue) >= max_concurrency:
                    thread_queue[0].join()
                    thread_queue.pop(0)

                if conduct_test(test, current, config, experiments_dir, logger=test_logger):
                    # Move to next test if function returns True
                    break
        except ExitEarly:
            return
        except Exception as ex:
            test_logger.error("failed to conduct test")
            test_logger.error(ex)
            test_logger.error(traceback.format_exc())


def conduct_test(test, current, config, experiments_dir, logger):
    logger.info("Starting test %s", test.id())
    logger.debug(test)

    test_experiment_dir = path.join(experiments_dir, test.experiment())
    if not path.exists(test_experiment_dir):
        logger.error("Test experiment directory %s not found", test_experiment_dir)
        return True

    config_sh_path = path.join(test_experiment_dir, "conf/config.sh")
    if not path.exists(config_sh_path):
        logger.error("Test experiment config file %s not found", config_sh_path)
        return True

    # Load test config
    test_config = ""
    with open(config_sh_path, "r") as config_file:
        test_config = config_file.read()

    # Provision experiment from cloudlab
    # Check for stopping before attempting to acquire mutex (might be poisoned)
    if stopping:
        raise ExitEarly()
    with cloudlab_lock:
        try:
            logger.info("Provisioning new experiment from cloudlab")
            old_logger = cloudlab.logger
            cloudlab.set_logger(logger)
            experiment = cloudlab.provision(profile=test.profile(), name=test.id())
        except ExitEarly:
            raise
        except OperationFailed as ex:
            logger.error("Could not provision experiment on cloudlab")
            current.failed(ex)
            return False
        except Exception as ex:
            logger.error("Encountered error while logging into cloudlab driver:")
            current.failed(ex)
            return False
        else:
            hostnames = "\n".join([f"│ {host}" for host in experiment.hostnames()])
            logger.info("Successfully provisioned new experiment from cloudlab: %s\n%s",
                        experiment, hostnames)
        finally:
            cloudlab.set_logger(old_logger)

    # Get hosts and then assign
    hosts = experiment.hostnames()
    executor_host = hosts[0]
    experiment_hosts = hosts[1:]
    hostname_options = assign_hosts(test_config, experiment_hosts)

    def replace_value(value):
        def replace_inner(match):
            if isinstance(value, str):
                val = f'"{value}"'
            else:
                val = str(value)
            return f'{match.group(1)}={val}'
        return replace_inner

    # Then, replace overrides
    options = {**test.options(), **hostname_options}
    for (key, value) in options.items():
        key_regex = re.compile(f'(?m)^((?:readonly )?{key})="?.*"?$')
        test_config = re.sub(key_regex, replace_value(value), test_config)

    # Create working directory
    work_dir = path.join("working", test.id())
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    logger.info("Using %s as the working directory", work_dir)

    config_sh_path = path.join(work_dir, "config.sh")
    try:
        with open(config_sh_path, "w") as rendered_config_file:
            rendered_config_file.write(test_config)
        logger.info("Wrote rendered config file to %s", config_sh_path)
    except IOError as ex:
        logger.error("Could not write rendered config to %s", config_sh_path)
        current.failed(ex)
        return False

    # Spawn thread to handle ssh/scp yielding
    log_path = path.join("logs", test.id() + ".log")
    results_path = path.join("results", test.id() + ".tar.gz")
    # pylint: disable=unexpected-keyword-arg
    test_thread = TestExecutionThread(test=test, hostname=executor_host,
                                      config_path=config_sh_path, log_path=log_path,
                                      results_path=results_path, config=config,
                                      experiment=experiment, cloudlab=cloudlab,
                                      cloudlab_lock=cloudlab_lock, logger=logger)
    test_thread.start()
    thread_queue.append(test_thread)
    return True


def assign_hosts(config_sh, hosts):
    assignments = {}
    host_fields = [match.group(1) for match in re.finditer(HOST_CONFIG_REGEX, config_sh)]
    num_hosts = len(hosts)
    num_fields = len(host_fields)
    hosts_per = num_hosts // num_fields
    hosts_extra = num_hosts % num_fields
    host_pool = hosts[:]
    for i, host_field in enumerate(host_fields):
        hosts_for_field = []
        for _ in range(hosts_per):
            hosts_for_field.append(host_pool.pop(0))
        # Add extra
        if i < hosts_extra:
            hosts_for_field.append(host_pool.pop(0))
        assignments[host_field] = hosts_for_field
    return {key: " ".join(value) for (key, value) in assignments.items()}


def flatten_tests(config: Dict[str, Any]) -> List[TestReplica]:
    """
    Flattens test replicas into a single list of TestReplicas
    """

    tests = config.get("tests", [])
    global_options = config.get("options", {})
    flattened = flatten_globals(tests, global_options)
    flattened = flatten_matrices(flattened)
    flattened = flatten_replicas(flattened)
    return flattened


def flatten_globals(tests: List[Dict[str, Any]],
                    global_options: Dict[str, Any]) -> List[Dict[str, Any]]:
    flattened = []
    for test_set in tests:
        options = {**test_set.get("options", {}), **global_options}
        test_set_new = test_set.copy()
        test_set_new["options"] = options
        flattened.append(test_set_new)
    return flattened


def flatten_matrices(tests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flattened = []
    for test_set in tests:
        if "matrix" in test_set:
            # Perform matrix config expansion
            intermediate = [test_set]
            dimensions = test_set["matrix"]
            for dimension in dimensions:
                name = dimension.get("name", "unknown")
                values = dimension.get("values", [])
                if not values:
                    continue

                new_intermediate = []
                # Expand the current working set
                for partial_config in values:
                    for test in intermediate:
                        test_id = test.get('id', None)
                        if not test_id:
                            break

                        value_id = partial_config.get('id', None)
                        if not value_id:
                            continue

                        new_id = f"{test_id}-{value_id}"
                        new_test = copy.deepcopy(test)
                        always_merger.merge(new_test, partial_config)

                        # Overwite ID
                        new_test["id"] = new_id

                        # Merge in matrix choice id
                        if "matrix_ids" not in new_test:
                            new_test["matrix_ids"] = {}
                        partial_matrix_ids = {}
                        partial_matrix_ids[name] = value_id
                        always_merger.merge(new_test["matrix_ids"], partial_matrix_ids)

                        new_intermediate.append(new_test)
                intermediate = new_intermediate
            flattened.extend(intermediate)
        else:
            flattened.append(test_set)
    return flattened


def flatten_replicas(tests: List[Dict[str, Any]]) -> List[TestReplica]:
    replicas_len = (len(str(test_set.get("replicas", 1))) for test_set in tests)
    # Use minimum id number of 2
    id_length = max(max(replicas_len), 2)
    test_id_fmt = f"{{}}-{{:0{id_length}}}"

    # Get list of completed tests to skip
    completed_tests = get_completed_tests("results")

    flattened = []
    for test_set in tests:
        test_id = test_set["id"]
        experiment = test_set["experiment"]
        replicas = test_set.get("replicas", 1)
        completed = test_set.get("completed", 0)
        profile = test_set.get("profile", None)
        options = test_set.get("options", {})
        matrix_ids = test_set.get("matrix_ids", None)

        for i in range(replicas - completed):
            j = i + completed
            # Make sure test hasn't been completed
            if test_id in completed_tests and j in completed_tests[test_id]:
                continue

            test_run_id = test_id_fmt.format(test_id, j)
            flattened.append(TestReplica(test_id=test_run_id, options=options,
                                         experiment=experiment, profile=profile,
                                         matrix_ids=matrix_ids))
    return flattened


def get_completed_tests(results_dir):
    completed_tests = {}
    try:
        files = find_files(results_dir, ".tar.gz")
        for f in files:
            filename = os.path.basename(f)
            match_obj = re.search(RESULT_TAR_REGEX, filename)
            if match_obj:
                test_id = match_obj.group(1)
                test_replica = int(match_obj.group(2))
                if test_id not in completed_tests:
                    completed_tests[test_id] = set()
                completed_tests[test_id].add(test_replica)
    except OSError:
        log.warning(
            "Could not detect completed experiments. Falling back to 'completed' value in test configs")
    return completed_tests


def find_files(path: str, extension: str) -> List[str]:
    """
    Gets a list of every file in the given path that has the given file extension
    """

    file_list = []
    for root, _, files in os.walk(path):
        for file in files:
            if file.endswith(extension):
                file_list.append(os.path.join(root, file))

    return file_list


def load_config(config_path: str) -> Dict[str, Any]:
    """
    Loads the config YAML file
    """

    config_dict = None
    try:
        with open(config_path, "r") as config_file:
            loader = yaml.Loader(config_file)
            config_dict = loader.get_data()
    except OSError as ex:
        log.error("An error ocurred during config file reading:")
        log.error(ex)
    except yaml.YAMLError as ex:
        log.error("An error ocurred during config YAML parsing:")
        log.error(ex)
    return config_dict


def find(data, value_path):
    """
    Gets an element in a deeply nested data structure
    """

    keys = value_path.split('.')
    inner = data
    for key in keys:
        if inner is None:
            return None
        else:
            inner = inner.get(key)
    return inner


def load_file(config, value_path):
    """
    Attempts to load the password from the config field
    """

    contents = None
    file_path = find(config, value_path)
    if file_path is not None:
        try:
            with open(file_path) as file_handle:
                contents = file_handle.read()
        except OSError as ex:
            log.error("An error ocurred during %s file reading:", value_path)
            log.error(ex)
    return contents


def join_all():
    log.info("Joining threads")
    for thread in thread_queue:
        try:
            thread.join()
        except:
            pass


def join_then_quit():
    stop()
    join_all()
    log.info("Exiting")
    sys.exit(1)


def force_handler(_signum, _frame):
    join_then_quit()


def exit_gracefully(_signum, _frame):
    signal.signal(signal.SIGINT, force_handler)

    try:
        if input("\nReally quit? (y/n)>\n").lower().startswith('y'):
            join_then_quit()

    except KeyboardInterrupt:
        join_then_quit()

    # restore the exit gracefully handler here
    signal.signal(signal.SIGINT, exit_gracefully)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, exit_gracefully)
    main()
