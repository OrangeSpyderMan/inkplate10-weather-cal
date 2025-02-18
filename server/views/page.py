import os
import logging
import subprocess

from time import sleep
from PIL import Image
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
            f.close()

        options = Options()
        service=Service(r"/usr/local/bin/geckodriver")
        options.add_argument("-headless")
        driver = webdriver.Firefox(service=service , options=options)

        try:
            driver.set_window_size(self.image_width, self.image_height)
            driver.get("file://" + html_fp)
            sleep(2)  # Wait for the page to load completely
            driver.save_full_page_screenshot(png_fp)
            self.log.info("Screenshot captured and saved to file.")
        except Exception as e:
            self.log.error("Screenshot failed to capture. Error: " + str(e))
        finally:
            driver.quit()
