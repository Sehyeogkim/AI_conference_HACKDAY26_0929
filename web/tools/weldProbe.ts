import loadMujoco from "@mujoco/mujoco";
const mujoco = await loadMujoco();
for (const [label, eq, extra] of [
  ["weld default", '<weld body1="t"/>', ""],
  ["weld solref", '<weld body1="t" solref="0.02 1"/>', ""],
  ["weld implicitfast", '<weld body1="t" solref="0.02 1"/>', 'integrator="implicitfast"'],
  ["weld implicitfast elliptic", '<weld body1="t" solref="0.02 1"/>', 'integrator="implicitfast" cone="elliptic" impratio="10"'],
  ["connect", '<connect body1="t" anchor="0 0 0"/>', 'integrator="implicitfast"'],
]) {
  const xml = `<mujoco><option timestep="0.002" ${extra}/><worldbody><body name="t" pos="1 0.6 0.8"><freejoint/><geom type="sphere" size="0.02" mass="0.02"/></body></worldbody><equality>${eq}</equality></mujoco>`;
  const model = mujoco.MjModel.from_xml_string(xml);
  const data = new mujoco.MjData(model);
  for (let i = 0; i < 250; i += 1) mujoco.mj_step(model, data);
  console.log(label.padEnd(28), "z after 0.5 s", data.xpos[5].toFixed(4));
}
