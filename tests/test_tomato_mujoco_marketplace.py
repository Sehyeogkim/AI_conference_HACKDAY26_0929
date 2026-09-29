"""Marketplace tests use the production HDF5 shape and a stubbed simulator replay."""

import json
import os
import time

import h5py
import numpy as np

from agriphilo.credits import Ledger
from agriphilo.game_qa import EpisodeVerdict
from agriphilo.tomato_mujoco_marketplace import CUSTOMER, TomatoMuJoCoMarketplace


def _episode(path, *, player="player.one", success=True):
    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        data.attrs["env"] = "TomatoHarvest"
        data.attrs["env_info"] = json.dumps({"control_freq": 20})
        demo = data.create_group("demo_1")
        demo.create_dataset("states", data=np.arange(100, dtype=float).reshape(20, 5) / 100)
        demo.create_dataset("actions", data=np.full((20, 2), 0.2))
        demo.attrs["model_file"] = "<mujoco/>"
        demo.attrs["ep_meta"] = "{}"
        demo.attrs["player_id"] = player
        demo.attrs["success"] = success
        demo.attrs["stage"] = 3
    old = time.time() - 5
    os.utime(path, (old, old))


def _market(tmp_path, replay):
    ledger = Ledger(tmp_path / "credits.json")
    ledger.topup(CUSTOMER, 100, "test-topup")
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    return TomatoMuJoCoMarketplace(
        ledger, episode_dir=episodes, root=tmp_path / "index", replay_qa=replay,
    )


def test_replay_pass_unlocks_reward_purchase_and_mining(tmp_path):
    calls = []

    def replay(path):
        calls.append(path)
        return EpisodeVerdict("demo_1", True, metrics={"replay_max_qpos_err": 0}, replay_checked=True)

    market = _market(tmp_path, replay)
    _episode(market.episode_dir / "harvest_1.hdf5")
    assert market.listing()["episodes_passed"] == 1
    assert market.listing()["episodes_passed"] == 1
    assert len(calls) == 1  # a GET does not rerun replay QA
    assert market.player_episodes("player.one")[0]["reward_credits"] > 0
    assert market.mine({}) == []  # requester must purchase first
    market.purchase(1)
    mined = market.mine({"min_quality": 3})
    assert len(mined) == 1
    assert mined[0]["download_url"].endswith("/harvest_1/file")
    assert market.file_path("harvest_1") == market.episode_dir / "harvest_1.hdf5"


def test_unavailable_replay_never_pays_or_mines(tmp_path):
    def replay(_path):
        raise RuntimeError("simulator missing")

    market = _market(tmp_path, replay)
    _episode(market.episode_dir / "harvest_1.hdf5")
    rec = market.scan()[0]
    assert rec["qa"] == "fail"
    assert not rec["replay_checked"]
    assert rec["reward_credits"] == 0
    assert market.file_path("harvest_1") is None
    assert market.mine({}) == []


def test_duplicate_hdf5_trace_is_rejected(tmp_path):
    market = _market(tmp_path, lambda _: EpisodeVerdict("demo_1", True, replay_checked=True))
    _episode(market.episode_dir / "harvest_1.hdf5")
    _episode(market.episode_dir / "harvest_2.hdf5")
    records = market.scan()
    assert records[0]["qa"] == "pass"
    assert records[1]["qa"] == "fail"
    assert "duplicate of an earlier episode" in records[1]["reasons"]


def test_changed_episode_cannot_be_purchased(tmp_path):
    market = _market(tmp_path, lambda _: EpisodeVerdict("demo_1", True, replay_checked=True))
    path = market.episode_dir / "harvest_1.hdf5"
    _episode(path)
    assert market.scan()[0]["qa"] == "pass"
    with path.open("ab") as stream:
        stream.write(b"changed after QA")
    assert market.file_path("harvest_1") is None
    assert market.mine({}) == []
    try:
        market.purchase(1)
    except ValueError as err:
        assert "changed after QA" in str(err)
    else:
        raise AssertionError("purchase should reject changed data")
