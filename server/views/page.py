import os
import logging

from time import sleep
from airium import Airium
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service


class Page:
    def __init__(
        self,
        name,
        width,
        height,
    ):
        self.name = name
        self.image_width = width
        self.image_height = height
        self.log = logging.getLogger(self.name)

        self.airium = Airium()

    def template(self, **kwargs):
        raise NotImplementedError(
            "Page {} should implement function {}".format(
                self.__class__.__name__, self.template.__name__
            )
        )

    def save(self):
        cwd = os.path.dirname(os.path.realpath(__file__))
        html_fp = os.path.join(cwd, "html", self.name + ".html")
        png_fp = os.path.join(cwd, self.name + ".png")

        with open(html_fp, "wb") as f:
            f.write(bytes(self.airium))

        options = Options()
        geckodriver_path = os.environ.get(
            "GECKODRIVER_PATH", "/usr/local/bin/geckodriver"
        )
        service = Service(geckodriver_path)
        options.add_argument("-headless")
        options.add_argument(f"--width={self.image_width}")
        options.add_argument(f"--height={self.image_height}")
        driver = webdriver.Firefox(service=service, options=options)

        try:
            driver.get("file://" + html_fp)
            self._resize_viewport(driver)
            sleep(2)  # Wait for the page to load completely
            self._resize_viewport(driver)
            driver.save_screenshot(png_fp)
            self.log.info("Screenshot captured and saved to file.")
        except Exception as e:
            self.log.error("Screenshot failed to capture. Error: " + str(e))
            raise
        finally:
            driver.quit()

    def _resize_viewport(self, driver):
        viewport = driver.execute_script(
            "return {width: window.innerWidth, height: window.innerHeight};"
        )
        width_delta = self.image_width - int(viewport["width"])
        height_delta = self.image_height - int(viewport["height"])
        if width_delta == 0 and height_delta == 0:
            return

        window_size = driver.get_window_size()
        driver.set_window_size(
            window_size["width"] + width_delta,
            window_size["height"] + height_delta,
        )
