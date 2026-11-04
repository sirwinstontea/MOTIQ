# Source documents (what the hardware numbers are based on)

All files were downloaded on **2026-11-04** from the manufacturers' own sites and stored unchanged in
`docs/sources/`. Text extracts (PyMuPDF for PDFs, HTML-to-text for web pages) are in `docs/text/` with
`=====PAGE n=====` markers, so every number in `profiles/*.yaml` can be traced. `pytest
tests/test_provenance.py` (or `hardware-capability-check --verify-profiles`) re-checks that each
quoted passage is really present in these files.

## Why this set of documents

The goal is a *complete* description of the UR5e as far as this checker needs it (kinematics, limits,
payload, tool interface, workspace). UR publishes no single document with all of it, so the primary
source is the **full user manual**, supplemented by the manufacturer's own articles for the parts the
manual omits (DH table / link masses, joint torques).

| File | What it is | Date / version | Used for | SHA-256 |
|---|---|---|---|---|
| `ur5e_user_manual_ps5_latest.pdf` | UR5e **PolyScope 5 User Manual**, 243 pp, official "latest" link | PDF created 2026-09-10 (© 2009-2026) | technical specs (sect. 3.1), payload vs CoG curve (9.1), tool flange/I-O (9.2-9.3), workspace & singularities (6.4), safety limits (10.3), stopping data (14.2) | `0a10ed59859b606f2e98eec0f295499daf6e5b40b70b3db1974c5324b9a1d4da` |
| `ur5e_user_manual_SW5_25_1.pdf` | UR5e User Manual for PolyScope 5.25.1 (710-965-00), 241 pp | 2026-09-11 | cross-check: identical specs (elbow +-160 deg, 4 m/s) | `2e6a3e17e59fd8435069421f52251429ee88c50440cf7e696e6846b6b72dbd3d` |
| `ur5e_user_manual_polyscopeX.pdf` | UR5e **PolyScope X** User Manual (SW 10.11), 183 pp | 2026-05-04 | cross-check for PolyScope X controllers (same arm) | `dde3dbbc4021a6d6ccd2bf4b2d7d1b3ec48c0b2324c7685b59a2a2608812948b` |
| `ur5e_datasheet_latest.pdf` | UR5e technical specification datasheet, "Updated October 2026" | 2026-10-20 | F/T sensor range & accuracy, mounting (any orientation), tool flange | `c09f8b4940a287f49256a4737a517c8813cb2aadab664cd06b8711b1f799f23b` |
| `ur5e_datasheet.pdf` | UR5e e-Series datasheet, "Updated December 2024" | 2024-12-10 | only to document differences (older) | `9f8fe19535441ca13fb7c34cdb3bd14fa2a7430b6c117e0d40ab3ce6413f00cd` |
| `dh_article.html` | UR article "DH Parameters for calculations of kinematics and dynamics" | retrieved 2026-11-04 | DH table (d, a, alpha), link masses, link centres of mass | `219b53973c935b1dc9b443c6214ce6974ee36f849c94f3d69d73495e34391004` |
| `ur_max_joint_torques.html` | UR article "Max. joint torques CB3 and e-Series" | created 2015-08-26 | joint torque ratings (size 3 = 150 Nm, size 1 = 28 Nm) | `f8532148b55a9d57b56f299d5a01ef6a1ab1ec0ac171b7d3448e0642be021510` |
| `ur_ros2_description/*.yaml` | UR's official ROS2 description repo, `config/ur5e/` | file last changed 2021-10-13 | cross-check of joint limits/kinematics only | joint_limits `1e908454...3c7`, default_kinematics `4ffc6086...0bc` |
| `realsense_d415_product_brief.pdf` | Intel RealSense D415 product brief | 2017-12-11 | depth/RGB FOV, resolution, dimensions | `374ff58a7b7fe8fc1f4c1c30d56a1d9c100e8a192eb8ce0c8a0708ac0a53701d` |
| Intel D415 spec page (online, quoted in `camera_realsense_d415.yaml`) | https://www.intel.com/content/www/us/en/products/sku/128256/... | retrieved 2026-11-04 | depth FOV H 69.4 deg / V 42.5 deg, ideal range 0.5-3 m | not archived |

## Is it up to date?

* The **PolyScope 5 manual at the official "latest" URL was created 2026-09-10**, the datasheet is dated
  October 2026, and the PolyScope X manual May 2026 - these are the newest official UR documents found.
* The three manuals agree on payload, reach, joint ranges (elbow +-160 deg), joint speed, repeatability, footprint,
  weight, IP rating, F/T accuracy and tool-bolt limit. Only the two PolyScope 5 manuals contain the wording
  "Maximum TCP speed ... 4 m/s" and "Tool Communication RS-485"; the PolyScope X manual words these differently
  (checked with a text search), so those two quotes cite the PolyScope 5 manual.

## Conflicts between official documents (and what the checker does)

| Item | Documents say | Used |
|---|---|---|
| **Elbow joint range** | PolyScope 5 / 5.25.1 / PolyScope X manuals: **+-160 deg**; datasheets (2024, 2026): +-360 deg for all joints; ROS2 repo: +-180 deg (artificial planning limit) | **+-160 deg** (most restrictive, newest manual) |
| Max TCP speed | 2026 manuals + datasheets: ~4 m/s; an older manual copy on ManualsLib: ~1 m/s | 4 m/s (irrelevant: controller safety limits are far lower) |
| Footprint / weight | Dec 2024 datasheet: 149 mm / 20.6 kg; Oct 2026 datasheet + manual: 151 mm / 20.7 kg | 2026 values |
| Safety functions | Dec 2024: 17; Oct 2026 datasheet: 23; manual: 21 | not used by the checker |

The ManualsLib link in the original request is a copy of an **older** manual (its tool I/O and speed numbers
differ) so the official PDFs above are used instead.

## Not available from official sources (so not invented)

* **Joint accelerations** - UR does not publish them (the ROS2 description repo says the same). Timing results are a
  lower bound from joint *speed* only.
* **Link radii / collision geometry** - not in the PDFs. The checker uses approximate capsule radii (flagged `assumed`)
  only to keep the arm above the table.
* **Per-robot calibration** (`calibration.conf`, up to a few mm) - not accessible without the real robot.
* **Official UR service manual PDF** - not found on universal-robots.com; the user manual already covers everything
  the checker uses.
* **Stopping distances/times** (manual 14.2) are plotted as figures only; they are a safety topic and not used here.
* **Intel D400 datasheet PDF** could not be fetched from intel.com; the D415 product brief (2017) plus Intel's
  specification page are used. The brief's "0.16 m minimum depth" contradicts Intel's own "ideal range ~0.5 m"
  and is **not** used.
* The **payload-vs-CoG curve** is a figure; its values were read from the PDF's vector drawing (flat 5 kg up to
  ~350 mm, 3.2 kg at 600 mm) - see `limits.payload_vs_cog_offset.extraction` in `profiles/ur5e.yaml`.
