"""Provider fixtures for contract tests.

Add a new provider by appending to PROVIDERS_TO_TEST. The contract tests will
automatically run against it.
"""

import os

import pytest_asyncio

from xbrain_memory.providers.native_stub import NativeStubProvider

PROVIDERS_TO_TEST: list[tuple[str, type]] = [
    ("native_stub", NativeStubProvider),
]

# Plan 02-03 will conditionally append:
#   if os.environ.get("RUN_MEM0_CONTRACT") == "1":
#       PROVIDERS_TO_TEST.append(("mem0", Mem0Provider))


@pytest_asyncio.fixture(
    params=PROVIDERS_TO_TEST,
    ids=[name for name, _ in PROVIDERS_TO_TEST],
)
async def provider(request):
    name, cls = request.param
    instance = cls()
    yield instance
    # Optional teardown — NativeStub has none. Future providers can add cleanup here.
