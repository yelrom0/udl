"""
--------------------------
- gallerydl_extractors.py -
--------------------------

gallery-dl with the tests' own extractor (tests/fixtures/gallery-dl/udltest.py), in either mode
"""

# System Imports
import importlib
import importlib.util
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

# Local Imports
from udl.plugins.downloaders.gallerydl import GalleryDlMode, GalleryDlOptions

EXTRACTORS: Final[Path] = Path(__file__).parent.parent / "fixtures" / "gallery-dl"


def _udltest_extractor() -> Any:
    spec = importlib.util.spec_from_file_location("udltest", EXTRACTORS / "udltest.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UdlTestExtractor


@contextmanager
def udltest_options(mode: GalleryDlMode) -> Generator[GalleryDlOptions]:
    """Options for a gallery-dl that has the udltest extractor.

    Its command line loads it from `extractor.module-sources`; in-process, it is added to
    gallery-dl's extractors (first, so it wins) for as long as this lasts.
    """
    if mode is GalleryDlMode.SUBPROCESS:
        yield GalleryDlOptions(
            mode=mode, config={"extractor": {"module-sources": [str(EXTRACTORS)]}}
        )
        return
    extractors = importlib.import_module("gallery_dl.extractor")
    extractor = _udltest_extractor()
    extractors.add(extractor)
    cache: list[Any] = extractors._cache
    cache.insert(0, cache.pop())
    try:
        yield GalleryDlOptions(mode=mode)
    finally:
        cache.remove(extractor)
