"""Tomato harvesting task using yesterday's RoboCasa PandaOmron coffee scene.

The same wheeled, single-arm robot and counter layout remain in place. The
player picks up a tomato and deposits it in a harvest tray. MuJoCo/robosuite
handles robot and object dynamics; the ordered stages are latched for replay.
"""

import numpy as np

from robocasa.environments.kitchen.kitchen import *


class TomatoHarvest(Kitchen):
    STAGES = [
        "Pick up the tomato",
        "Place the tomato in the harvest tray",
    ]

    def _setup_kitchen_references(self):
        super()._setup_kitchen_references()
        self.coffee_machine = self.register_fixture_ref(
            "coffee_machine", dict(id=FixtureType.COFFEE_MACHINE)
        )
        self.counter = self.register_fixture_ref(
            "counter", dict(id=FixtureType.COUNTER, ref=self.coffee_machine)
        )
        self.harvest_counter = self.counter
        self.init_robot_base_ref = self.coffee_machine

    def get_ep_meta(self):
        ep_meta = super().get_ep_meta()
        ep_meta["lang"] = "Pick up the tomato and place it in the harvest tray."
        return ep_meta

    def _get_obj_cfgs(self):
        return [
            dict(
                name="tomato",
                obj_groups="tomato",
                placement=dict(
                    fixture=self.counter,
                    sample_region_kwargs=dict(ref=self.coffee_machine),
                    size=(0.12, 0.12),
                    pos=("ref", -1.0),
                    offset=(-0.17, 0.02),
                    rotation=(np.pi / 2, np.pi / 2),
                ),
            ),
            dict(
                name="harvest_tray",
                obj_groups="tray",
                placement=dict(
                    fixture=self.counter,
                    sample_region_kwargs=dict(ref=self.coffee_machine),
                    size=(0.5, 0.5),
                    pos=("ref", -1.0),
                    offset=(-0.4, -0.05),
                    rotation=(0, 0),
                ),
            ),
        ]

    def _reset_internal(self):
        super()._reset_internal()
        for _ in range(300):
            self.sim.step()
        self.stage = 0

    def update_stages(self):
        """Advance from grasp to tray placement in a replay-stable order."""
        if not hasattr(self, "stage"):
            self.stage = 0
        if self.stage == 0 and OU.check_obj_grasped(self, "tomato"):
            self.stage = 1
        if self.stage == 1 and OU.check_obj_in_receptacle(
            self, "tomato", "harvest_tray"
        ) and not OU.check_obj_grasped(self, "tomato"):
            self.stage = 2
        return self.stage

    def _post_action(self, action):
        self.update_stages()
        return super()._post_action(action)

    def _check_success(self):
        return getattr(self, "stage", 0) >= len(self.STAGES)
