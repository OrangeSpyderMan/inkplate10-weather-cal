import os
import logging
import subprocess

from time import sleep
from PIL import Image
from airium import Airium

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
        browser=subprocess.run([
            '/usr/bin/firefox',
            '--headless',
            '--new-instance',
            '--purgecaches',
            '--screenshot=' + png_fp,  
            '--window-size=' + str(self.image_width) + ',' + str(self.image_height),
            '--url=file://' + html_fp
        ] , stdout=subprocess.DEVNULL , stderr=subprocess.DEVNULL , check=True )
        if browser.returncode != 0:
            browsererror=repr(browser.stderr)
            self.log.error("Screenshot failed to capture.")
            self.log.error("The following error occurred :")
            self.log.error(browsererror)
        else:
            self.log.info("Screenshot captured and saved to file.")
