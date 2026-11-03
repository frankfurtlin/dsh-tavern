from types import SimpleNamespace

from app.completion_policy import input_ceiling


def test_world_ledger_and_director_fit_without_changing_reader_allowance():
    profile = SimpleNamespace(context_window=1048576)
    # A populated, curated world: 16,987 bytes of game data and a 9,432-byte
    # director prompt. Both must fit; counting the ledger alone is insufficient.
    request_size = 16987 + 9432 + 256
    assert request_size <= input_ceiling(profile, 2048, world=True) <= 96000
    assert input_ceiling(profile, 2048) == 12000


def test_world_allowance_reserves_completion_and_transport_in_small_windows():
    profile = SimpleNamespace(context_window=32768)
    assert input_ceiling(profile, 2048, world=True) == 30208
    profile.context_window = 4096
    assert input_ceiling(profile, 2048, world=True) == 1536
