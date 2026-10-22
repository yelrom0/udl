"""
--------------
- udltest.py -
--------------

A gallery-dl extractor module for the tests: for http://localhost:<port>/udltest/<kind> it yields
what <kind> says, using the fixture site's files. gallery-dl can load extractor modules of your
own (its command line from `extractor.module-sources`), so this one is a plain module of its own.
"""

# pyright: basic
# (gallery-dl is untyped, and this subclasses its Extractor)

# Package Imports
from gallery_dl import exception
from gallery_dl.extractor.common import Extractor, Message


class UdlTestExtractor(Extractor):
    category = "udltest"
    subcategory = "gallery"
    filename_fmt = "{filename}.{extension}"
    pattern = r"(http://localhost:\d+)/udltest/(\w+)"

    def items(self):
        root, kind = self.groups
        yield Message.Directory, "", {}
        if kind == "partial":  # a file, then one that is not found
            yield Message.Url, root + "/media/pixel.png", {"filename": "pixel", "extension": "png"}
            yield Message.Url, root + "/media/missing.png", {"filename": "gone", "extension": "png"}
        elif kind == "ytdl":  # a video gallery-dl hands to yt-dlp
            yield (
                Message.Url,
                f"ytdl:{root}/media/sample.mp4",
                {"filename": "v", "extension": "mp4"},
            )
        elif kind == "unreachable":  # a file on a server that is not running
            yield Message.Url, "http://localhost:1/x.png", {"filename": "x", "extension": "png"}
        elif kind == "error":  # the site says the gallery is gone
            raise exception.NotFoundError("gallery")
        # "empty": nothing
