"""
Driver for cloudlab.us website interaction and experiment provisioning
"""

import re
import urllib
import traceback
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from execution.retry import retry
from execution.log import with_logger

NOT_ENOUGH_REGEX = re.compile(
    r'[0-9]+ nodes of type .+ requested, but only [0-9]+ available nodes of type .+ found')
SSH_REGEX = re.compile(r'ssh -p [0-9]+ \S+@(\S+)')


class ProvisionedExperiment():
    def __init__(self, uuid, name):
        self._uuid = uuid
        self._name = name

    def uuid(self):
        return self._uuid

    def name(self):
        return self._name

    def __repr__(self):
        return f"{self._name} ({self._uuid})"


class Experiment(ProvisionedExperiment):
    def __init__(self, uuid, name, hostnames):
        ProvisionedExperiment.__init__(self, uuid, name)
        self._hostnames = hostnames

    def hostnames(self):
        return self._hostnames


@with_logger
class Cloudlab():
    def __init__(self, username, password, profile, headless):
        options = Options()
        options.headless = headless
        options.add_argument("window-size=1920,1080")
        self._driver = webdriver.Chrome(chrome_options=options)
        self._driver.implicitly_wait(1)
        self._username = username
        self._password = password
        self._authenticated = False
        self._profile = profile

    def login(self, retry_count=5):
        driver = self._driver
        task = "log into Cloudlab"
        for current in retry(retry_count, task=task, logger=self.logger):  # pylint: disable=unexpected-keyword-arg
            driver.get("https://www.cloudlab.us/login.php")
            WebDriverWait(driver, 60).until(lambda driver: driver.execute_script(
                'return document.readyState') == 'complete')

            if 'User Dashboard' in driver.title:
                self._authenticated = True
                return
            elif 'Login' in driver.title:
                self._authenticated = False
            else:
                url, title = driver.current_url, driver.title
                current.failed(f'unknown page reached "{title}" @ {url}"')
                continue

            try:
                driver.find_element(By.NAME, "uid").click()
                driver.find_element(By.NAME, "uid").send_keys(self._username)
                driver.find_element(By.NAME, "password").send_keys(self._password)
                driver.find_element(By.NAME, "login").click()
            except Exception as ex:
                current.failed("could not interact with login form", ex)
                continue

            WebDriverWait(driver, 60).until(lambda driver: driver.execute_script(
                'return document.readyState') == 'complete')

            if 'User Dashboard' in driver.title:
                self._authenticated = True
                return
            elif 'Login' not in driver.title:
                url, title = driver.current_url, driver.title
                current.failed(f'unknown page reached "{title}" @ {url}"')

    def terminate(self, experiment, retry_count=5):
        driver = self._driver
        task = f"terminate experiment {experiment} on Cloudlab"
        for current in retry(retry_count, task=task, logger=self.logger):  # pylint: disable=unexpected-keyword-arg
            try:
                if not self._authenticated:
                    self.login(retry_count=retry_count)
            except Exception as ex:
                current.failed("could not log in", ex)
                continue

            driver.get(
                f"https://www.cloudlab.us/status.php?uuid={experiment.uuid()}")
            WebDriverWait(driver, 60).until(lambda driver: driver.execute_script(
                'return document.readyState') == 'complete')
            # Make sure we're authenticated
            if 'Login' in driver.title:
                self._authenticated = False
                try:
                    self.login(retry_count=retry_count)
                except Exception as ex:
                    current.failed("could not log in", ex)
                    continue

            # Expand header if collapsed
            try:
                WebDriverWait(driver, 15).until(expected_conditions.visibility_of_element_located(
                    (By.ID, "terminate_button")))
            except (NoSuchElementException, TimeoutException):
                WebDriverWait(driver, 15).until(expected_conditions.presence_of_element_located(
                    (By.XPATH, "//a[@id='profile_status_toggle']")))
                driver.find_element(
                    By.XPATH, "//a[@id='profile_status_toggle']").click()
                WebDriverWait(driver, 15).until(expected_conditions.visibility_of_element_located(
                    (By.ID, "terminate_button")))
                try:
                    term_button = driver.find_element_by_id("terminate_button")
                except NoSuchElementException as ex:
                    current.failed(
                        f"terminate button could not be found even after expanding", ex)
                    continue

            try:
                # Click terminate and confirm
                WebDriverWait(driver, 240).until(expected_conditions.element_to_be_clickable(
                    (By.ID, "terminate_button")))
                term_button = driver.find_element_by_id("terminate_button")
                term_button.click()
                WebDriverWait(driver, 4).until(expected_conditions.element_to_be_clickable(
                    (By.CSS_SELECTOR, "#terminate_modal #terminate")))
                driver.find_element_by_css_selector(
                    "#terminate_modal #terminate").click()
            except TimeoutError:
                current.failed("could not wait on terminate pathway to become clickable", ex)
            else:
                self.info("Terminated experiment %s", experiment)
                return

    def provision(self, name=None, expires_in=5, retry_count=5):
        driver = self._driver
        task = f"provision experiment {f'with name {name} ' if name is not None else ''}on Cloudlab"
        for current in retry(retry_count, task=task, logger=self.logger):  # pylint: disable=unexpected-keyword-arg
            try:
                if not self._authenticated:
                    self.login(retry_count=retry_count)
            except Exception as ex:
                current.failed("could not log in", ex)
                continue

            driver.get("https://www.cloudlab.us/instantiate.php")
            WebDriverWait(driver, 60).until(lambda driver: driver.execute_script(
                'return document.readyState') == 'complete')

            # Make sure we're authenticated
            if "Login" in driver.title:
                self._authenticated = False
                try:
                    self.login(retry_count=retry_count)
                except Exception as ex:
                    current.failed("could not log in", ex)
                    continue

            try:
                WebDriverWait(driver, 15).until(expected_conditions.element_to_be_clickable(
                    (By.ID, "change-profile")))
                driver.find_element(By.ID, "change-profile").click()
                # Wait for page to select initial profile (otherwise the selection will be cleared)
                WebDriverWait(driver, 15).until(expected_conditions.presence_of_element_located(
                    (By.CSS_SELECTOR, "li.profile-item.selected")))
                driver.find_element(
                    By.XPATH, f"//li[@name='{self._profile}']").click()
                WebDriverWait(driver, 15).until(expected_conditions.presence_of_element_located(
                    (By.XPATH, f"//li[@name='{self._profile}' and contains(@class, 'selected')]")))
                driver.find_element(
                    By.XPATH, "//button[contains(text(),'Select Profile')]").click()
                WebDriverWait(driver, 30).until(expected_conditions.element_to_be_clickable(
                    (By.LINK_TEXT, "Next")))
                driver.find_element(By.LINK_TEXT, "Next").click()

                # Set name if given
                if name is not None:
                    driver.find_element(By.ID, "experiment_name").click()
                    driver.find_element(
                        By.ID, "experiment_name").send_keys(name)

                WebDriverWait(driver, 15).until(expected_conditions.element_to_be_clickable(
                    (By.LINK_TEXT, "Next")))
                driver.find_element(By.LINK_TEXT, "Next").click()
                WebDriverWait(driver, 15).until(expected_conditions.element_to_be_clickable(
                    (By.ID, "experiment_duration")))
                driver.find_element(By.ID, "experiment_duration").click()
                driver.find_element(By.ID, "experiment_duration").clear()
                driver.find_element(
                    By.ID, "experiment_duration").send_keys(str(expires_in))
                WebDriverWait(driver, 15).until(expected_conditions.element_to_be_clickable(
                    (By.LINK_TEXT, "Finish")))
                driver.find_element(By.LINK_TEXT, "Finish").click()
            except Exception as ex:
                current.failed(ex)
                continue

            try:
                # Wait until the info page has been loaded
                WebDriverWait(driver, 60).until(
                    expected_conditions.title_contains("Experiment Status"))
                WebDriverWait(driver, 60).until(lambda driver: driver.execute_script(
                    'return document.readyState') == 'complete')
            except TimeoutException as ex:
                # Can't really clean up if an error ocurrs here, so hope it doesn't
                if 'Login' in driver.title:
                    current.failed('not logged in', ex)
                    self._authenticated = False
                    continue
                elif 'Instantiate' in driver.title:
                    current.failed('still on instantiate page after wait', ex)
                    continue
                else:
                    url, title = driver.current_url, driver.title
                    current.failed(f'unknown page reached "{title}" @ {url}"')
                    continue

            # Consider the experiment provisioned here, so any failures from here on need
            # to be cleaned up (experiment terminated)
            WebDriverWait(driver, 60).until(expected_conditions.presence_of_element_located(
                (By.XPATH, "//td[contains(.,'Name:')]/following-sibling::td")))
            exp_name = driver.find_element_by_xpath(
                "//td[contains(.,'Name:')]/following-sibling::td").text
            url_parts = urllib.parse.urlparse(driver.current_url)
            uuid = urllib.parse.parse_qs(url_parts.query).get("uuid")[0]
            experiment = ProvisionedExperiment(uuid, exp_name)
            self.info(f"Instantiating experiment {experiment}")

            # Wait on status until "ready" or something else
            status_xpath = "//span[@id='quickvm_status']"
            status = driver.find_element_by_xpath(status_xpath).text
            if status != "ready":
                self.debug(f"Waiting for experiment to become ready")

            failed = False
            while status != "ready":
                try:
                    WebDriverWait(driver, 4).until(
                        expected_conditions.text_to_be_present_in_element((By.XPATH, status_xpath),
                                                                          "ready"))
                except TimeoutException:
                    status = driver.find_element_by_xpath(status_xpath).text
                    if status == "terminating":
                        # Already terminating; back off for 5 minutes and try again
                        current.failed("experiment is marked as terminating")
                        failed = True
                        break
                    elif status == "ready":
                        break
                    elif status == 'created' or status == 'provisioning' or status == 'booting':
                        # Good; keep waiting
                        continue
                    else:
                        # If "failed" or otherwise, assume failure; need to clean up
                        # Try to extract error
                        cloudlab_error = self.get_error_text()
                        self.error("Experiment is marked as %s: stopping; trying to terminate. %s",
                                   status, self.get_error_text())
                        self.safe_terminate(experiment, retry_count=retry_count)
                        if "Resource reservation violation" in cloudlab_error:
                            current.failed('resource reservation violation')
                        elif re.search(NOT_ENOUGH_REGEX, cloudlab_error):
                            current.failed('insufficient nodes available')
                        else:
                            current.failed('error during provisioning')
                        failed = True
                        break
                else:
                    status = "ready"
                    break

            if failed or status != "ready":
                continue

            try:
                # Navigate to list panel
                WebDriverWait(driver, 15).until(
                    expected_conditions.visibility_of_element_located((By.ID, "show_listview_tab")))
                driver.find_element(By.ID, "show_listview_tab").click()
            except (TimeoutException, NoSuchElementException) as ex:
                self.warning(
                    "An error ocurred while attempting to expand the experiment listview")
                error_text = self.get_error_text()
                if error_text:
                    self.warning(error_text)
                current.failed("could not expand the experiment listview!", ex)
                self.debug("Terminating experiment %s", experiment)
                self.safe_terminate(experiment, retry_count=retry_count)
                continue

            # Should be ready here, read hostnames
            ssh_commands = [elem.text for elem in driver.find_elements_by_xpath(
                "//td[@name='sshurl']//kbd")]
            if not ssh_commands:
                current.failed("parsed hostnames list was empty")
                error_text = self.get_error_text()
                if error_text:
                    self.warning(error_text)
                self.debug("Terminating experiment %s", experiment)
                self.safe_terminate(experiment, retry_count=retry_count)
                continue
            hostnames = []
            for ssh_command in ssh_commands:
                match_obj = re.search(SSH_REGEX, ssh_command)
                if match_obj:
                    hostnames.append(match_obj.group(1))

            # Experiment successfully provisioned, hostnames extracted
            return Experiment(experiment.uuid(), experiment.name(), hostnames)

    def safe_terminate(self, experiment, retry_count=5):
        try:
            self.terminate(experiment, retry_count)
        except Exception as ex:
            self.warning("An exception ocurred while attempting to terminate %s:", experiment)
            self.warning(ex)
            self.warning(traceback.format_exc())

    def try_extract_error(self):
        driver = self._driver
        top_status = driver.find_element_by_id("status_message").text
        if top_status == "Something went wrong!":
            try:
                WebDriverWait(driver, 15).until(
                    expected_conditions.visibility_of_element_located((By.ID, "error_panel")))
            except TimeoutException:
                return None
            error_text_elem = driver.find_element_by_id("error_panel_text")
            if error_text_elem is not None:
                return error_text_elem.text
        return None

    def get_error_text(self):
        error = self.try_extract_error()
        return f"(Cloudlab error:\n{error})" if error is not None else ""
