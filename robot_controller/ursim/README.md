# URSim (UR5e controller simulator)

The real UR controller software (PolyScope 5.x) running in Docker with **no hardware**. Use it to develop and test
a UR RTDE adapter before connecting to the real arm.

```bash
# either (what we use):
mkdir -p "$HOME/ursim-ur5e/programs"
docker run -d --rm --name ursim-ur5e -e ROBOT_MODEL=UR5 \
  -p 127.0.0.1:5900:5900 -p 127.0.0.1:6080:6080 -p 127.0.0.1:29999:29999 \
  -p 127.0.0.1:30001-30004:30001-30004 \
  -v "$HOME/ursim-ur5e/programs:/ursim/programs" universalrobots/ursim_e-series
# or (same ports, so only one at a time):
docker compose -f ursim/docker-compose.yml up -d      # from robot_controller/
xdg-open http://127.0.0.1:6080/vnc.html               # PolyScope in the browser
printf 'robotmode\nquit\n' | nc 127.0.0.1 29999       # dashboard server check
docker compose -f ursim/docker-compose.yml down
```

At boot the robot is in `DISCONNECTED` mode. Power it on and release the brakes in PolyScope, or from the
dashboard with `power on` and then `brake release`.

The ports are 29999 (dashboard), 30001–30003 (primary/secondary/real-time) and 30004 (RTDE). All of them are
bound to `127.0.0.1`. Programs are saved to `ursim/programs/`.

Tasks run on URSim with `--backend ursim_sync`, which executes on URSim and mirrors into PyBullet (see the main
README). That backend accepts only loopback hosts. `physical_disabled` still refuses all real-arm motion. See
`docs/SAFETY_BOUNDARIES.md` for the path from URSim to the real arm.
