"""One concrete way to reach a model: an OpenAI-compatible endpoint.

:class:`crawler.annotate.LLMAnnotator` deliberately takes a bare
``complete(prompt) -> text`` callable; this module supplies the one such
callable most estates can actually deploy — a chat-completions call against
any OpenAI-compatible server (a vendor API, a vLLM or Ollama service inside
the cluster). It is the only place in the package that knows a wire protocol
exists, and it is optional twice over: the ``openai`` client library is an
extra (``pip install okf[llm]``), and nothing imports this module unless a
run asks for the ``llm`` annotator.

Secrets follow the house rule (config: no secrets in files): the API key is
read from an environment variable, never passed as a value. Endpoints that
do not check keys (a cluster-local server) still get one — set the variable
to a dummy; an unset variable is a configuration error, not a shrug, because
the same config against a vendor endpoint would otherwise fail differently.

The call itself is as boring as it can be made: one user message, the whole
prompt, temperature 0. Retries, JSON repair and fallbacks are deliberately
absent — a model that cannot answer is treated exactly like no model at all
(:mod:`crawler.annotate` substitutes the explicit unknown and reports it),
and a transport that flakes should be visible, not papered over.
"""

from __future__ import annotations

import os

from .annotate import LLMAnnotator
from .errors import ConfigError

#: Environment variables the CLI's ``--annotator llm`` reads.
MODEL_ENV = "CRAWLER_LLM_MODEL"
BASE_URL_ENV = "CRAWLER_LLM_BASE_URL"
API_KEY_ENV = "CRAWLER_LLM_API_KEY"
TIMEOUT_ENV = "CRAWLER_LLM_TIMEOUT"

#: Seconds one completion may take. Generous by default: a cluster-local
#: model reasoning over a wide legacy table is slow, and the alternative to
#: waiting is an explicit-unknown description a human then has to write.
DEFAULT_TIMEOUT = 300.0


def openai_compatible(
    *,
    model: str,
    base_url: str | None = None,
    api_key_env: str = API_KEY_ENV,
    timeout: float = DEFAULT_TIMEOUT,
    client_factory=None,
):
    """A ``complete(prompt) -> text`` callable for an OpenAI-compatible API.

    ``base_url`` of None means the client library's default (the vendor
    endpoint); a cluster-local server names its own, e.g.
    ``http://ollama.tools.svc:11434/v1``. ``client_factory`` exists for
    tests: anything returning an object with
    ``.chat.completions.create(...)`` stands in for the real client.
    """
    key = os.environ.get(api_key_env)
    if not key:
        raise ConfigError(
            f"the llm annotator reads its API key from ${api_key_env}, which "
            "is not set; endpoints that ignore keys still need a dummy value "
            "there, so that this config against a keyed endpoint fails here "
            "and not halfway through an annotation run"
        )

    if client_factory is None:
        try:
            from openai import OpenAI
        except ImportError:
            raise ConfigError(
                "the llm annotator needs the optional 'openai' client "
                "library; install the extra: pip install okf[llm]"
            ) from None
        client_factory = OpenAI
    client = client_factory(base_url=base_url, api_key=key, timeout=timeout)

    def complete(prompt: str) -> str:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content
        return content or ""

    return complete


def annotator_from_env(client_factory=None) -> LLMAnnotator:
    """The CLI's ``--annotator llm``: everything from the environment.

    ``$CRAWLER_LLM_MODEL`` is required; ``$CRAWLER_LLM_BASE_URL`` optional
    (unset means the vendor default); the key comes from
    ``$CRAWLER_LLM_API_KEY`` as above; ``$CRAWLER_LLM_TIMEOUT`` optionally
    stretches the per-completion timeout, in seconds.
    """
    model = os.environ.get(MODEL_ENV)
    if not model:
        raise ConfigError(
            f"the llm annotator needs ${MODEL_ENV} to name the model "
            f"(and optionally ${BASE_URL_ENV} for a non-default endpoint)"
        )
    raw_timeout = os.environ.get(TIMEOUT_ENV)
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
    except ValueError:
        raise ConfigError(
            f"${TIMEOUT_ENV} must be a number of seconds, got {raw_timeout!r}"
        ) from None
    return LLMAnnotator(
        openai_compatible(
            model=model,
            base_url=os.environ.get(BASE_URL_ENV) or None,
            timeout=timeout,
            client_factory=client_factory,
        )
    )


__all__ = [
    "API_KEY_ENV",
    "BASE_URL_ENV",
    "MODEL_ENV",
    "annotator_from_env",
    "openai_compatible",
]
