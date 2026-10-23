"""
------------
- files.py -
------------

Chain files: sites' tag chains as YAML, a document each (`---` between them), as
`udl chain export` writes them and `udl chain import` reads them (by default, every *.yaml file
in ~/.config/udl/sites):

    site: example.com
    pattern: ^/watch/           # optional: by default the whole site
    fetch: static               # static | browser
    headers: {Referer: "{url}"} # optional: {url} is the page's URL
    steps:
      - select: iframe#player
        attr: src
      - follow: true
      - select: video > source[type="video/mp4"]
        attr: src
        pick: best              # first | all | best
    handoff: http               # optional: by default http for a file, yt-dlp for a stream
    priority: 0                 # optional
    requires_auth: false        # optional

A document can also be a list of chains, and a chain's steps can be its one-line shorthand
(`steps: iframe#player@src >> video source@src`).
"""

# System Imports
from collections.abc import Sequence
from typing import Any

# Package Imports
import yaml
from pydantic import ValidationError

# Local Imports
from udl.chains.models import SiteChain
from udl.errors import ConfigError


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, problem['loc'])) or 'chain'}: {problem['msg']}"
        for problem in error.errors()
    )


def read_chains(text: str) -> list[SiteChain]:
    """The chains in a chain file's text. Raises ConfigError, which says which chain is wrong
    (counting from 1) and why."""
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as e:
        raise ConfigError(f"The chain file is not valid YAML: {e}") from e
    found: list[Any] = []
    for document in documents:
        if document is not None:  # (an empty document)
            found += document if isinstance(document, list) else [document]
    chains: list[SiteChain] = []
    for number, item in enumerate(found, start=1):
        if not isinstance(item, dict):
            raise ConfigError(
                f"The chain file's chain {number}: a chain is a mapping (site:, steps: ...), "
                f"not {item!r}"
            )
        try:
            chains.append(SiteChain.model_validate(item))
        except ValidationError as e:
            raise ConfigError(f"The chain file's chain {number}: {_problems(e)}") from e
    return chains


def _document(chain: SiteChain) -> dict[str, Any]:
    """A chain as a chain file writes it: what it says, in the order above."""
    document: dict[str, Any] = {"site": chain.site}
    if chain.pattern is not None:
        document["pattern"] = chain.pattern
    document["fetch"] = chain.fetch.value
    if chain.headers:
        document["headers"] = dict(chain.headers)
    document["steps"] = [
        step.model_dump(mode="json", exclude_defaults=True) for step in chain.steps
    ]
    if chain.handoff is not None:
        document["handoff"] = chain.handoff
    if chain.priority:
        document["priority"] = chain.priority
    if chain.requires_auth:
        document["requires_auth"] = True
    return document


def write_chains(chains: Sequence[SiteChain]) -> str:
    """Chains as a chain file's text: a YAML document each."""
    if not chains:
        return ""
    # (Not allow_unicode: PyYAML would write characters such as U+0085 as they are, which read
    # back as line breaks. Escaped, every text reads back as it was.)
    return yaml.safe_dump_all([_document(chain) for chain in chains], sort_keys=False)
