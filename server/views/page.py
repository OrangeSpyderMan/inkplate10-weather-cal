import os
import logging
from time import sleep
from PIL import Image
from airium import Airium
from selenium.common.exceptions import WebDriverException

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

        driver = self._get_webdriver()
        driver.get("file://" + html_fp)
        driver.save_screenshot(png_fp)
        driver.quit()

        self.log.info("Screenshot captured and saved to file.")

    def _get_webdriver(self):
        geckpath = Service(executable_path=r'/usr/local/bin/geckodriver')
        opts = Options()
        opts.add_argument("-headless")
        
        try:
            driver = webdriver.Firefox(service = geckpath , options=opts)
        except WebDriverException as wde:
            raise wde 

        driver.set_window_rect(width=self.image_width, height=self.image_height)

        return driver
