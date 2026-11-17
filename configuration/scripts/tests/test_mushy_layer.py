#!/usr/bin/env python3
"""Reproducible, public-interface mushy-layer tests for the Icepack driver.

Python 3.10+ and the standard library are sufficient.  Builds use icepack.setup,
the selected machine environment and the repository Makefile, not edited copies
of column physics.  A working native Fortran toolchain is required for ``run``.

Examples (from the checkout root)::

    python3 configuration/scripts/tests/test_mushy_layer.py list
    python3 configuration/scripts/tests/test_mushy_layer.py fetch-data --suite extended
    python3 configuration/scripts/tests/test_mushy_layer.py run --machine conda \\
        --environment macos --suite core --output /absolute/new/results
    python3 configuration/scripts/tests/test_mushy_layer.py run --machine conda \\
        --environment macos --tests T1,T7,T10-brine --offline --output /new/results
    python3 configuration/scripts/tests/test_mushy_layer.py validate --results /new/results

Missing *selected* inputs are downloaded by default.  Use fetch-data before
moving to an offline compute node; --offline never attempts network access.
Inputs are pinned, checksum verified, and extracted into a private cache, never
the machine's shared ICEPACK_DATA directory.  Outputs must be new directories.
Scientific failures return 1, infrastructure/input failures return 2.  Execution
success is not evidence that a convergence criterion has been met.
"""

from __future__ import annotations

import argparse
import colorsys
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    title: str
    core: bool
    kind: str = "acceptance"
    datasets: tuple[str, ...] = ()


CASES = {
    "T1": Case("Analytical binary-mixture closure", True),
    "T2": Case("Compiled phase/frazil and default-off regression", True),
    "T3": Case("Freshwater column and restart continuation", True),
    "T4": Case("Fresh basal growth: one-step and two-step", True),
    "T5": Case("4/7/10/14/20/28-layer convergence", False, "convergence"),
    "T6": Case("Topographic pond freshwater feedback", True),
    "T7": Case("Permeability and pond flushing connectivity", True),
    "T8": Case("Whole-column water/salt and local heat budgets", True),
    "T9": Case("Static observed-profile adaptation", False, "descriptive", ("profiles",)),
    "T10-brine": Case("Controlled and integrated brine phase physics", True),
    "T10-BGC": Case("Active BGC scaling initializer and guard", True),
    "T10": Case("Annual freshwater namelist matrix", False),
    "T10-cost": Case("Four-cell debug and I/O-included cost", False, "descriptive"),
    "T11": Case("Two-year prescribed salinity-notch sensitivity", False,
                "descriptive", ("forcing",)),
    "T12": Case("Prescribed-forcing 7/10/14/20/28-layer convergence", False,
                "convergence", ("forcing",)),
    "T13": Case("Prescribed-forcing flag off/on sensitivity", False,
                "descriptive", ("forcing",)),
    "T14": Case("Paired freshening and freezing-point ocean boundary", False,
                "acceptance", ("forcing",)),
}

# Versioned upstream inputs.  The observation file is CC BY 4.0; the forcing
# record supplies the Icepack sample input distribution and its provenance.
DATASETS = {
    "forcing": {
        "url": "https://zenodo.org/api/records/3728287/files/Icepack_data-20200326.tar.gz/content",
        "filename": "Icepack_data-20200326.tar.gz",
        "sha256": "eb706ec09d4d35d6da3a118c8cba79d7d727e3d6aed19568e24423b92d3ea6bc",
        "source": "https://doi.org/10.5281/zenodo.3728287",
        "attribution": "Icepack sample forcing, version 20200326; see upstream record.",
    },
    "profiles": {
        "url": "https://doi.pangaea.de/10.1594/PANGAEA.971385?format=textfile",
        "filename": "mosaic_mcs_fyi_v2.tsv",
        "sha256": "f7a13410394126aac95a56e218988f7ee8a4e4abe512c0e7b618c520df350f81",
        "source": "https://doi.org/10.1594/PANGAEA.971385",
        "attribution": "MOSAiC first-year ice cores, PANGAEA.971385, CC BY 4.0.",
    },
}
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?")
RESULT_SCHEMA = 4
FRESH = {
    "npt": "48", "dumpfreq": "'d'", "dump_last": ".true.",
    "history_format": "'none'", "tr_pond_lvl": ".false.",
    "tr_pond_topo": ".false.", "mushy_zero_salinity": ".true.", "saltmax": "0.0",
    "sss_fixed": "0.0", "ice_ref_salinity": "0.0", "saltflux_option": "'prognostic'",
    "atm_data_type": "'default'", "ocn_data_type": "'default'",
    "mushy_diagnostics": ".true.", "mushy_diagfreq": "1",
    "mushy_profilefreq": "1",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if sys.version_info >= (3, 11) else _digest(stream)


def _digest(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def extract_data(archive: Path, target: Path) -> None:
    """Extract only bounded regular files/directories, with no links/devices."""
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if sum(member.size for member in members) > 256 * 1024 * 1024:
            raise ValueError("input archive exceeds the 256 MiB uncompressed limit")
        for member in members:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or ".." in name.parts or "\\" in member.name
                    or not (member.isfile() or member.isdir())):
                raise ValueError(f"unsafe archive member: {member.name}")
        for member in members:
            destination = target / member.name
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = source.extractfile(member)
                if stream is None:
                    raise ValueError(f"unreadable archive member: {member.name}")
                with stream, destination.open("xb") as output:
                    shutil.copyfileobj(stream, output)


def acquire(name: str, cache: Path, offline: bool) -> Path:
    spec = DATASETS[name]
    cache.mkdir(parents=True, exist_ok=True)
    saved = cache / spec["filename"]
    if saved.exists():
        if not saved.is_file() or sha256(saved) != spec["sha256"]:
            raise ValueError(f"cached input checksum mismatch: {saved}; remove it explicitly to refetch")
    elif offline:
        raise FileNotFoundError(f"offline input unavailable: {saved}; run fetch-data first")
    else:
        # A partial response must never look like a validated cache entry.
        with tempfile.TemporaryDirectory(prefix="download-", dir=cache) as temporary:
            partial = Path(temporary) / "input"
            curl = shutil.which("curl")
            if curl:
                # System curl uses native TLS trust on platforms where Python
                # does not consult the OS keychain.  Never disable TLS checks.
                completed = subprocess.run(
                    [curl, "--fail", "--location", "--proto", "=https",
                     "--proto-redir", "=https", "--max-time", "120",
                     "--max-filesize", str(32 * 1024 * 1024), "--silent", "--show-error",
                     "--output", str(partial), spec["url"]], check=False)
                if completed.returncode:
                    raise RuntimeError(f"dataset download failed ({completed.returncode}): {name}")
            else:
                request = urllib.request.Request(spec["url"], headers={"User-Agent": "Icepack-mushy-tests/1"})
                with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as stream:
                    size = 0
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > 32 * 1024 * 1024:
                            raise ValueError(f"download exceeds the 32 MiB limit: {name}")
                        stream.write(block)
            if sha256(partial) != spec["sha256"]:
                raise ValueError(f"download checksum mismatch: {name}; upstream bytes changed")
            # Atomic publication; an existing entry is never silently replaced.
            os.link(partial, saved)
    if name != "forcing":
        return saved
    extracted = cache / spec["sha256"]
    if not extracted.exists():
        with tempfile.TemporaryDirectory(prefix="extract-", dir=cache) as temporary:
            unpacked = Path(temporary) / "data"
            unpacked.mkdir()
            extract_data(saved, unpacked)
            unpacked.rename(extracted)
    # Verify extracted files against the pinned archive on every cache reuse.
    with tarfile.open(saved, "r:gz") as archive:
        for member in archive.getmembers():
            if member.isfile():
                actual = extracted / member.name
                source = archive.extractfile(member)
                if source is None or not actual.is_file():
                    raise ValueError(f"incomplete extracted cache: {actual}")
                with source:
                    expected = _digest(source)
                if sha256(actual) != expected:
                    raise ValueError(f"modified extracted cache: {actual}")
    return extracted


def selected(tests: str | None, suite: str) -> list[str]:
    result = tests.split(",") if tests else [key for key, case in CASES.items() if suite == "extended" or case.core]
    if not result or len(set(result)) != len(result) or any(key not in CASES for key in result):
        raise ValueError("--tests must be a comma-separated selection from list, without duplicates")
    return result


def namelist(text: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        pattern = rf"(?im)^(\s*{re.escape(name)}\s*=\s*)[^!\n]*"
        text, count = re.subn(pattern, lambda match: match[1] + value + "\n", text)
        if count != 1:
            raise ValueError(f"expected exactly one native namelist setting: {name} (found {count})")
    return text


def setting(text: str, name: str, value: str) -> str:
    text, count = re.subn(rf"(?m)^(setenv\s+{name}\s+).*",
                          lambda match: match[1] + value, text)
    if count != 1:
        raise ValueError(f"missing/ambiguous native setting: {name}")
    return text


def command(argv: list[str], cwd: Path, log: Path) -> float:
    start = time.perf_counter()
    with log.open("a", encoding="utf-8") as stream:
        stream.write("$ " + repr(argv) + "\n")
        stream.flush()
        completed = subprocess.run(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, check=False)
    if completed.returncode:
        raise RuntimeError(f"command exited {completed.returncode}; see {log}")
    return time.perf_counter() - start


def csv_rows(path: Path) -> list[dict[str, float]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = [{key: (1.0 if value == "T" else 0.0 if value == "F" else float(value))
                 for key, value in record.items()} for record in reader]
    if not rows or any(not math.isfinite(value) for row in rows for value in row.values()):
        raise ValueError(f"missing or nonfinite diagnostic samples: {path}")
    return rows


def check(name: str, actual: float, limit: float = 0.0) -> dict[str, object]:
    if not math.isfinite(actual) or not math.isfinite(limit) or limit < 0:
        raise ValueError(f"invalid scientific metric: {name}")
    return {"name": name, "actual": actual, "limit": limit, "passed": abs(actual) <= limit}


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty evidence: {path}")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def stage_forcing(run: Path, sources: tuple[Path, Path]) -> dict[str, str]:
    """Keep short namelist filenames in the native NICE directory layout."""
    directory = run / "forcing" / "NICE_2015"
    directory.mkdir(parents=True)
    for source, name in zip(sources, ("atmosphere.txt", "ocean.txt")):
        shutil.copyfile(source, directory / name)
    return {"data_dir": "'./forcing'", "atm_data_file": "'atmosphere.txt'",
            "ocn_data_file": "'ocean.txt'"}


class Runner:
    """Own isolated native builds/runs; never patch or instrument model sources."""

    def __init__(self, args: argparse.Namespace, data: dict[str, Path]):
        self.args = args
        self.root = args.icepack_root.resolve()
        self.output = args.output.resolve()
        self.data = data
        self.builds: dict[tuple[int, bool, bool, bool], tuple[Path, Path, str]] = {}

    def build(self, layers: int = 7, bgc: bool = False, reference: bool = False,
              pinned: bool = False) -> tuple[Path, Path, str]:
        key = (layers, bgc, reference, pinned)
        if key in self.builds:
            return self.builds[key]
        root = self.root
        if reference:
            root = self.output / "reference-source"
            command(["git", "clone", "--shared", "--no-checkout", str(self.root), str(root)],
                    self.output, self.output / "reference.log")
            command(["git", "checkout", "--detach", self.args.baseline_ref], root,
                    self.output / "reference.log")
        unique = hashlib.sha256(str(self.output).encode()).hexdigest()[:10]
        case = self.output / f"build-{unique}-{layers}-{'bgc' if bgc else 'pinned' if pinned else 'thermo'}-{'ref' if reference else 'current'}"
        setup = [str(root / "icepack.setup"), "--case", str(case),
                 "--mach", self.args.machine, "--env", self.args.environment, "--set", "debug"]
        command(setup, root, self.output / "setup.log")
        settings = case / "icepack.settings"
        text = settings.read_text()
        for name, value in {"ICE_RUNDIR": str(case / "run"), "NICELYR": str(layers),
                            "NSNWLYR": "5" if pinned else "1",
                            "TRSNOW": "1" if pinned else "0",
                            "TRAGE": "0" if pinned else "1",
                            "TRFY": "0" if pinned else "1",
                            "ICE_SNICARHC": "true" if pinned else "false",
                            "TRBRI": "1" if bgc else "0",
                            "TRBGCZ": "1" if bgc else "0",
                            "TRALG": "1" if bgc else "0",
                            "TRDOC": "1" if bgc else "0"}.items():
            text = setting(text, name, value)
        settings.write_text(text)
        build_seconds = command(["./icepack.build"], case, case / "build.log")
        write_json(case / "build-timing.json", {"native_build_wall_seconds": build_seconds})
        executable = case / "run" / "icepack"
        if not executable.is_file():
            raise FileNotFoundError(f"native build did not produce {executable}")
        template = (case / "icepack_in").read_text()
        self.builds[key] = (case, executable, template)
        return self.builds[key]

    def model(self, name: str, values: dict[str, str] | None = None, layers: int = 7,
              reference: bool = False, checkpoint: Path | None = None, pinned: bool = False,
              bgc: bool = False, forcing: tuple[Path, Path] | None = None) -> Path:
        case, executable, template = self.build(layers=layers, reference=reference, pinned=pinned, bgc=bgc)
        run = self.output / name
        run.mkdir()
        (run / "restart").mkdir()
        changes = dict(FRESH)
        changes.update(values or {})
        if forcing is not None:
            changes.update(stage_forcing(run, forcing))
        if checkpoint:
            changes.update(restart=".true.", ice_ic=f"'{checkpoint}'")
        if reference:
            # A reference checkout need not contain the new diagnostic namelist.
            changes.pop("mushy_diagnostics", None)
            changes.pop("mushy_diagfreq", None)
            changes.pop("mushy_profilefreq", None)
        (run / "icepack_in").write_text(namelist(template, changes))
        shutil.copy2(executable, run / "icepack")
        # Source the *real* machine environment rather than guessing FC/library
        # paths or pretending an uninstalled environment is active.
        launch = f'source "{case}/icepack.settings"; source "{case}/env.{self.args.machine}_{self.args.environment}"; cd "{run}"; exec /usr/bin/time -p ./icepack'
        elapsed = command(["/bin/csh", "-f", "-c", launch], case, run / "run.log")
        log = (run / "run.log").read_text()
        if "ICEPACK COMPLETED SUCCESSFULLY" not in log:
            raise RuntimeError(f"Icepack did not complete successfully: {run}")
        timing = {field: re.findall(rf"(?m)^{field}\s+([0-9.]+)\s*$", log) for field in ("real", "user", "sys")}
        if any(len(values) != 1 for values in timing.values()):
            raise ValueError(f"missing/ambiguous executable timing records: {run}")
        write_json(run / "timing.json", {"wall_seconds": float(timing["real"][0]),
                    "cpu_seconds": float(timing["user"][0]) + float(timing["sys"][0]),
                    "startup_inclusive_wall_seconds": elapsed,
                    "scope": "four-cell debug executable including diagnostic/restart I/O, excluding machine-environment startup"})
        return run

    def harness(self, name: str, source: str, bgc: bool = False,
                files: dict[str, str] | None = None) -> Path:
        case, _, template = self.build(bgc=bgc)
        run = self.output / name
        run.mkdir()
        (run / "restart").mkdir()
        changes = dict(FRESH)
        if bgc:
            changes.update(tr_brine=".true.", tr_bgc_C=".true.", scale_bgc=".true.", solve_zbgc=".true.")
        (run / "icepack_in").write_text(namelist(template, changes))
        (run / "harness.F90").write_text(source)
        for filename, text in (files or {}).items():
            (run / filename).write_text(text)
        objects = sorted((case / "run" / "compile").glob("*.o"))
        objects = [str(path) for path in objects if path.name != "icedrv_MAIN.o"]
        objdir = case / "run" / "compile"
        macros = case / f"Macros.{self.args.machine}_{self.args.environment}"
        makefile = (
            f"include {macros}\n"
            f"harness:\n\t$(FC) $(FFLAGS) $(FREEFLAGS) -I{objdir} "
            f"-o harness harness.F90 {' '.join(objects)} $(LDFLAGS) $(SLIBS)\n"
        )
        (run / "harness.mk").write_text(makefile)
        launch = f'source "{case}/icepack.settings"; source "{case}/env.{self.args.machine}_{self.args.environment}"; cd "{run}"; make -f harness.mk && ./harness'
        command(["/bin/csh", "-f", "-c", launch], case, run / "harness.log")
        return run


# Small, readable client programs exercise only the supported icepack_intfc
# boundary.  They are test clients, not replacement/instrumented physics.
PHASE_CLIENT = r"""
program phase_client
 use icepack_intfc, dbl_kind => icepack_dbl_kind
 implicit none
 integer :: i, j, u
 real(dbl_kind) :: rhoi, rhow, lf, ci, cw, delta, cap, initphi
 real(dbl_kind) :: q, t, phi, off_t, off_phi, s, new_s, new_t, new_q, melt_t
 real(dbl_kind) :: factors(18), state_salts(18), salts(53)
 logical :: default_flag
 call icepack_configure()
 call check_warnings()
 call icepack_query_parameters(mushy_zero_salinity_out=default_flag)
 call check_warnings()
 call icepack_init_parameters(ktherm_in=2,mushy_zero_salinity_in=.true.,Tliquidus_max_in=@CAP@)
 call check_warnings()
 call icepack_query_parameters(rhoi_out=rhoi,rhow_out=rhow,Lfresh_out=lf, &
      cp_ice_out=ci,cp_ocn_out=cw,dSin0_frazil_out=delta, &
      Tliquidus_max_out=cap,phi_init_out=initphi)
 call check_warnings()
 open(newunit=u,file='constants.csv',status='new')
 write(u,'(a)') 'rhoi,rhow,latent,cp_ice,cp_ocn,delta,cap,phi_init,default_flag'
 write(u,'(*(g0,:,","))') rhoi,rhow,lf,ci,cw,delta,cap,initphi,default_flag
 close(u)
 factors=[-1.1d0,-1.d0,-0.999999999999d0,-0.75d0,-0.5d0,-0.25d0,0.d0,-1.d-12,1.d-12,0.1d0, &
          -1.1d0,-0.75d0,-0.5d0,0.d0,0.1d0,-0.75d0,-0.75d0,-0.75d0]
 state_salts=0.d0
 state_salts(11:15)=4.d0
 state_salts(16:18)=[1.d-8,1.d-5,1.d-3]
 open(newunit=u,file='phase.csv',status='new')
 write(u,'(a)') 'q,salinity,temperature,phi,off_temperature,off_phi'
 do i=1,size(factors)
   q=factors(i)*rhoi*lf
   call icepack_mushy_state(zqin=q,zSin=state_salts(i),zTin=t,phi=phi)
   call check_warnings()
   off_t=t
   off_phi=phi
   if(state_salts(i)>0.d0) then
     call icepack_init_parameters(mushy_zero_salinity_in=.false.)
     call check_warnings()
     call icepack_mushy_state(zqin=q,zSin=state_salts(i),zTin=off_t,phi=off_phi)
     call check_warnings()
     call icepack_init_parameters(mushy_zero_salinity_in=.true.)
     call check_warnings()
   endif
   write(u,'(*(g0,:,","))') q,state_salts(i),t,phi,off_t,off_phi
 enddo
 close(u)
 salts=[0.d0,1.d-12,1.d-10,1.d-8,1.d-6,1.d-5,1.d-4,1.d-3,1.d-2,0.1d0, &
        0.25d0,0.5d0,0.75d0,1.d0,1.25d0,1.5d0,1.75d0,2.d0,2.25d0,2.5d0, &
        2.75d0,3.d0,3.25d0,3.5d0,3.75d0,4.d0,4.25d0,4.5d0,4.75d0,5.d0, &
        5.25d0,5.5d0,5.75d0,5.999d0,6.d0,6.001d0,6.25d0,6.5d0,6.75d0,7.d0, &
        7.25d0,7.5d0,7.75d0,8.d0,8.25d0,8.5d0,8.75d0,9.d0,9.25d0,9.5d0,9.75d0,10.d0,35.d0]
 open(newunit=u,file='frazil.csv',status='new')
 write(u,'(a)') 'sss,salt,temperature,q,phi,liquidus'
 do j=1,size(salts)
   s=salts(j)
   call icepack_mushy_frazil_state(sss=s,zSin=new_s,zTin=new_t,zqin=new_q)
   call check_warnings()
   call icepack_mushy_state(zqin=new_q,zSin=new_s,zTin=t,phi=phi)
   call check_warnings()
   melt_t=icepack_liquidus_temperature(Sin=new_s/initphi)
   call check_warnings()
   write(u,'(*(g0,:,","))') s,new_s,new_t,new_q,phi,melt_t
 enddo
 close(u)
 contains
 subroutine check_warnings()
   call icepack_warnings_flush(6)
   if(icepack_warnings_aborted()) error stop 'public phase interface failed'
 end subroutine
end program
"""


def phase_client(cap: float = -0.1) -> str:
    return PHASE_CLIENT.replace("@CAP@", f"{cap:.16e}".replace("e", "d"))

CONNECTIVITY_CLIENT = r"""
program connectivity_client
 use icepack_intfc, dbl_kind => icepack_dbl_kind
 implicit none
 integer :: i,u
 real(dbl_kind) :: rhoi,lf,rhofresh,rhow,cp_ocn,qpond,q,t,phi,perm,w
 real(dbl_kind) :: fractions(3),temperatures(7),phis(7)
 call icepack_configure()
 call check_warnings()
 call icepack_init_parameters(ktherm_in=2,mushy_zero_salinity_in=.true.)
 call check_warnings()
 call icepack_init_tracer_sizes(ncat_in=5,nilyr_in=7,nslyr_in=1)
 call check_warnings()
 call icepack_init_tracer_flags(tr_pond_in=.true.,tr_pond_topo_in=.true.)
 call check_warnings()
 call icepack_query_parameters(rhoi_out=rhoi,Lfresh_out=lf,rhofresh_out=rhofresh, &
      rhow_out=rhow,cp_ocn_out=cp_ocn)
 call check_warnings()
 qpond=cp_ocn*rhow*0.2d0
 fractions=[0.01d0,0.05d0,0.2d0]
 open(newunit=u,file='connectivity.csv',status='new')
 write(u,'(a)') 'target_phi,phi,permeability,velocity,water_flux,salt_flux,enthalpy_flux'
 do i=1,size(fractions)
   q=(fractions(i)-1.d0)*rhoi*lf
   call icepack_mushy_state(zqin=q,zSin=0.d0,zTin=t,phi=phi)
   call check_warnings()
   perm=icepack_mushy_permeability(phi=phi)
   call check_warnings()
   temperatures=t
   phis=phi
   call icepack_mushy_flushing_velocity(zTin=temperatures,phi=phis,hin=2.d0, &
        hsn=0.d0,hilyr=2.d0/7.d0,hpond=0.1d0,apond=0.5d0,dt=3600.d0,w=w)
   call check_warnings()
   write(u,'(*(g0,:,","))') fractions(i),phi,perm,w,rhofresh*w,0.d0,qpond*w
 enddo
 close(u)
 contains
 subroutine check_warnings()
   call icepack_warnings_flush(6)
   if(icepack_warnings_aborted()) error stop 'public connectivity interface failed'
 end subroutine
end program
"""

BRINE_CLIENT = r"""
program brine_client
 use icepack_intfc, dbl_kind => icepack_dbl_kind
 implicit none
 integer :: i,k,u,v,count
 real(dbl_kind) :: rhoi,lf,ci,factor,perm,minphi
 real(dbl_kind) :: tracers(14),bg(5),cg(8),ig(4),factors(6),q(3),before(3)
 real(dbl_kind) :: t(5),phi(5),salt(5),bs(5),brho(5),it(4),ip(4),ir(4),ibs(4),d(4),is(4)
 call icepack_configure()
 call check_warnings()
 call icepack_init_parameters(ktherm_in=2,mushy_zero_salinity_in=.true.)
 call check_warnings()
 call icepack_init_tracer_sizes(ncat_in=1,nilyr_in=7,nblyr_in=3,nslyr_in=1,ntrcr_in=14)
 call check_warnings()
 call icepack_init_tracer_indices(nt_qice_in=1,nt_sice_in=8)
 call check_warnings()
 call icepack_query_parameters(rhoi_out=rhoi,Lfresh_out=lf,cp_ice_out=ci)
 call check_warnings()
 call icepack_init_hbrine(bgrid_out=bg,igrid_out=ig,cgrid_out=cg)
 call check_warnings()
 factors=[1.1d0,1.d0,1.d0-1.d-12,0.75d0,0.25d0,0.d0]
 open(newunit=v,file='profiles.in',status='old')
 read(v,*) count
 open(newunit=u,file='brine.csv',status='new')
 write(u,'(a)') 'profile,layer,q,expected_q,phi_before,phi_after,temperature,permeability,brine_salinity'
 do i=1,7+count
   tracers=0.d0
   if(i<=7) then
     do k=1,7
       if(i==7) then
         factor=1.2d0-0.8d0*cg(k+1)
       else
         factor=factors(i)
       endif
       tracers(k)=-rhoi*lf*factor
     enddo
   else
     read(v,*) tracers
   endif
   t=0.d0
   phi=0.d0
   salt=0.d0
   bs=0.d0
   brho=0.d0
   it=0.d0
   ip=0.d0
   ir=0.d0
   ibs=0.d0
   d=0.d0
   is=0.d0
   call icepack_mushy_microstructure(trcrn=tracers,hice_old=2.d0,hbr_old=2.d0, &
        sss=0.d0,sst=0.d0,bTin=t,iTin=it,bphin=phi,kperm=perm,bphi_min=minphi, &
        bSin=salt,brine_sal=bs,brine_rho=brho,iphin=ip,ibrine_rho=ir, &
        ibrine_sal=ibs,iDin=d,iSin=is,enthalpy_out=q,liquid_fraction_before_out=before)
   call check_warnings()
   do k=1,3
     factor=q(k)
     if(i<=6) factor=-rhoi*lf*factors(i)
     if(i==7) factor=-rhoi*lf*(1.2d0-0.8d0*bg(k+1))
     write(u,'(*(g0,:,","))') i,k,q(k),factor,before(k),phi(k+1),t(k+1),perm,bs(k+1)
   enddo
 enddo
 close(u)
 close(v)
 contains
 subroutine check_warnings()
   call icepack_warnings_flush(6)
   if(icepack_warnings_aborted()) error stop 'public brine interface failed'
 end subroutine
end program
"""

BGC_CLIENT = r"""
program bgc_client
 use icepack_intfc, dbl_kind => icepack_dbl_kind
 use icedrv_InitMod, only: icedrv_initialize
 use icedrv_state, only: trcrn
 use icedrv_domain_size, only: nilyr,ncat
 use, intrinsic :: ieee_arithmetic, only: ieee_is_finite
 implicit none
 integer :: nbtrcr,ntrcr,ntrcr_o,max_nbtrcr
 real(dbl_kind), allocatable :: salt(:,:),bio(:,:),ocean(:)
 call icedrv_initialize()
 call check_warnings()
 call icepack_query_tracer_sizes(nbtrcr_out=nbtrcr,ntrcr_out=ntrcr, &
      ntrcr_o_out=ntrcr_o,max_nbtrcr_out=max_nbtrcr)
 call check_warnings()
 if(nbtrcr<=0) error stop 'BGC test requires an active tracer'
 allocate(salt(nilyr,ncat),bio(ntrcr-ntrcr_o,ncat),ocean(max_nbtrcr))
 ocean=1.d0
 salt=0.d0
 bio=0.d0
 call icepack_init_bgc(sicen=salt,trcrn=bio,sss=0.d0,ocean_bio_all=ocean)
 call check_warnings()
 if(any(bio/=0.d0)) error stop 'freshwater BGC initializer produced nonzero tracers'
 print *, 'BGC_ZERO_OK'
 salt=1.d-12
 bio=0.d0
 call icepack_init_bgc(sicen=salt,trcrn=bio,sss=1.d-12,ocean_bio_all=ocean)
 call check_warnings()
 if(.not.all(ieee_is_finite(bio))) error stop 'tiny-salt BGC scaling is nonfinite'
 print *, 'BGC_TINY_OK'
 ! The invalid state must abort; no subsequent physics call is made.
 salt=1.d0
 bio=0.d0
 call icepack_init_bgc(sicen=salt,trcrn=bio,sss=0.d0,ocean_bio_all=ocean)
 call icepack_warnings_flush(6)
 if(.not.icepack_warnings_aborted()) error stop 'BGC zero-denominator guard failed'
 print *, 'BGC_GUARD_OK'
 contains
 subroutine check_warnings()
   call icepack_warnings_flush(6)
   if(icepack_warnings_aborted()) error stop 'public BGC interface failed'
 end subroutine
end program
"""


def last_restart(run: Path) -> Path:
    files = sorted((run / "restart").glob("iced.*"))
    if not files:
        raise FileNotFoundError(f"no restart evidence: {run}")
    return files[-1]


def freshwater_state(q: float, constants: dict[str, float]) -> tuple[float, float]:
    latent = constants["rhoi"] * constants["latent"]
    if q < -latent:
        return (q + latent) / (constants["rhoi"] * constants["cp_ice"]), 0.0
    if q <= 0:
        return 0.0, 1 + q / latent
    return q / (constants["rhow"] * constants["cp_ocn"]), 1.0


def zero_sss_frazil_checks(rows: list[dict[str, float]]) -> list[dict[str, object]]:
    produced = [row for row in rows if row["frazil_water_kg_m2"] > 0]
    return [
        check("nonvacuous exact-zero-SSS frazil production", float(not produced)),
        check("SSS during exact-zero-SSS frazil production (ppt)",
              max((abs(row["sss_ppt"]) for row in produced), default=1.0)),
        check("new-frazil salinity at exact-zero SSS (ppt)",
              max((abs(row["new_frazil_salinity_ppt"]) for row in produced), default=1.0)),
        check("frazil salt-correction source at exact-zero SSS (kg/m2)",
              max((abs(row["frazil_correction_salt_source_kg_m2"])
                   for row in produced), default=1.0)),
    ]


def phase_checks(run: Path) -> tuple[list[dict[str, object]], dict[str, float]]:
    constants = csv_rows(run / "constants.csv")[0]
    errors = [check("zero-salinity flag defaults off", constants["default_flag"])]
    states = csv_rows(run / "phase.csv")
    if len(states) != 18 or sum(row["salinity"] > 0 for row in states) != 8:
        raise ValueError(f"incomplete compiled phase-state coverage: {run}")
    for row in states:
        if row["salinity"] == 0:
            t, phi = freshwater_state(row["q"], constants)
            errors.extend([check("freshwater temperature (C)", row["temperature"] - t, 1e-10),
                           check("freshwater phase fraction", row["phi"] - phi, 1e-12)])
        else:
            for field, off_field in (("temperature", "off_temperature"), ("phi", "off_phi")):
                tolerance = max(1e-12, 1e-6 * max(abs(row[field]), abs(row[off_field])))
                errors.append(check("positive-salinity flag parity " + field,
                                    (row[field] - row[off_field]) / tolerance, 1.0))
        reconstructed = (constants["rhoi"] * constants["cp_ice"] * row["temperature"]
            + row["phi"] * (constants["rhow"] * constants["cp_ocn"] -
               constants["rhoi"] * constants["cp_ice"]) * row["temperature"]
            - (1 - row["phi"]) * constants["rhoi"] * constants["latent"])
        errors.append(check("compiled enthalpy reconstruction (J/m3)", reconstructed - row["q"], 1e-6))
    frazil = csv_rows(run / "frazil.csv")
    if (len(frazil) != 53 or len({row["sss"] for row in frazil}) != 53
            or not any(row["sss"] == 0 for row in frazil)):
        raise ValueError(f"incomplete frazil salinity sweep: {run}")
    for row in frazil:
        s = row["sss"]
        delta = constants["delta"]
        expected = s - delta if s > 2 * delta else s * s / (4 * delta)
        errors.extend([check("native frazil salt (ppt)", row["salt"] - expected, 1e-12),
                       check("capped frazil temperature (C)",
                             row["temperature"] - min(row["liquidus"], constants["cap"]), 1e-10)])
        if row["liquidus"] <= constants["cap"]:
            errors.append(check("uncapped frazil liquid fraction",
                                row["phi"] - constants["phi_init"], 1e-10))
        reconstructed = (constants["rhoi"] * constants["cp_ice"] * row["temperature"]
            + row["phi"] * (constants["rhow"] * constants["cp_ocn"] -
               constants["rhoi"] * constants["cp_ice"]) * row["temperature"]
            - (1 - row["phi"]) * constants["rhoi"] * constants["latent"])
        errors.append(check("native frazil enthalpy reconstruction (J/m3)",
                            reconstructed - row["q"], 1e-6))
    return errors, constants


def layer_checks(run: Path, fresh: bool = False) -> list[dict[str, object]]:
    rows = csv_rows(run / "mushy_layers.csv")
    checks = [
        check("negative liquid fraction", min(0.0, min(row["phi"] for row in rows)), 1e-12),
        check("liquid fraction above one", max(0.0, max(row["phi"] for row in rows) - 1), 1e-12),
        check("negative salinity (ppt)", min(0.0, min(row["sice_ppt"] for row in rows)), 1e-12),
    ]
    if fresh:
        checks.append(check("freshwater salinity (ppt)", max(abs(row["sice_ppt"]) for row in rows)))
    return checks


def t13_profiles(run: Path, layers: int, days: int = 730) -> dict:
    """Read complete daily active-category profiles; absence means no active ice."""
    profiles = {}
    for row in csv_rows(run / "mushy_layers.csv"):
        step, cell, category, layer = (int(row[name]) for name in ("step", "cell", "category", "layer"))
        if (any(row[name] != value for name, value in zip(("step", "cell", "category", "layer"),
                                                           (step, cell, category, layer)))
                or step % 48 or not 1 <= step // 48 <= days
                or cell not in (2, 3) or not 1 <= category <= 5 or not 1 <= layer <= layers):
            raise ValueError(f"invalid T13 ice profile index in {run}")
        key = (step // 48, cell, category)
        profile = profiles.setdefault(key, {"ice": {}, "snow": {},
                                            "area": row["aicen_fraction"],
                                            "ice_volume": row["vicen_m"],
                                            "snow_volume": row["vsnon_m"]})
        if (layer in profile["ice"] or row["aicen_fraction"] <= 0 or row["vicen_m"] <= 0
                or row["vsnon_m"] < 0 or any(profile[name] != row[source] for name, source in (
                    ("area", "aicen_fraction"), ("ice_volume", "vicen_m"), ("snow_volume", "vsnon_m")))):
            raise ValueError(f"duplicate or inconsistent T13 ice profile in {run}: {key}")
        profile["ice"][layer] = row
    for row in csv_rows(run / "mushy_snow.csv"):
        step, cell, category, layer = (int(row[name]) for name in ("step", "cell", "category", "layer"))
        key = (step // 48, cell, category)
        if (any(row[name] != value for name, value in zip(("step", "cell", "category", "layer"),
                                                           (step, cell, category, layer)))
                or step % 48 or key not in profiles or not 1 <= layer <= 5
                or layer in profiles[key]["snow"]):
            raise ValueError(f"invalid or duplicate T13 snow profile in {run}: {key}")
        profiles[key]["snow"][layer] = row
    for key, profile in profiles.items():
        if set(profile["ice"]) != set(range(1, layers + 1)) or set(profile["snow"]) != set(range(1, 6)):
            raise ValueError(f"incomplete T13 layer profile in {run}: {key}")
    if {key[1] for key in profiles} != {2, 3}:
        raise ValueError(f"T13 requires active profiles in both target cells: {run}")
    return profiles


def t13_series(profiles: dict, cell: int, category: int | None, layers: int, days: int = 730) -> list:
    """Volume-weighted layer states and category thickness; None marks absent ice/snow."""
    definitions = (("snow", "temperature_C", 5), ("ice", "temperature_C", layers),
                   ("ice", "sice_ppt", layers), ("ice", "phi", layers))
    result = []
    for kind, field, count in definitions:
        curves = []
        for layer in range(1, count + 1):
            values = []
            for day in range(1, days + 1):
                group = [profiles[(day, cell, cat)] for cat in range(1, 6)
                         if (category is None or cat == category) and (day, cell, cat) in profiles]
                weight = "snow_volume" if kind == "snow" else "ice_volume"
                total = sum(profile[weight] for profile in group)
                values.append(sum(profile[kind][layer][field] * profile[weight] for profile in group) / total
                              if total > 0 else None)
            curves.append(values)
        result.append(curves)
    for field in ("ice_volume", "snow_volume"):
        curves = []
        for cat in ((category,) if category is not None else range(1, 6)):
            curves.append([profiles[(day, cell, cat)][field] / profiles[(day, cell, cat)]["area"]
                           if (day, cell, cat) in profiles and profiles[(day, cell, cat)][field] > 0 else None
                           for day in range(1, days + 1)])
        result.append(curves)
    return result


def t13_plot(path: Path, left: list, right: list, title: str, days: int = 730,
             category: int | None = None) -> None:
    """Draw paired daily profiles without connecting gaps in inactive categories."""
    labels = ("Snow temperature (C)", "Ice temperature (C)", "Ice salinity (ppt)",
              "Liquid volume fraction", "Category ice thickness (m)", "Category snow thickness (m)")
    colors = ("#332288", "#3366aa", "#0088aa", "#22aa77", "#77aa33",
              "#ccaa00", "#dd7744", "#bb3344", "#882255", "#663366")
    def layer_color(index: int, count: int) -> str:
        rgb = colorsys.hls_to_rgb(0.75 * (1 - index / max(1, count - 1)), 0.38, 0.8)
        return "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)

    elements = ['<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1800" viewBox="0 0 1400 1800">',
                '<rect width="1400" height="1800" fill="white"/>',
                f'<text x="700" y="30" text-anchor="middle" font-size="22">{html.escape(title)}</text>',
                '<text x="375" y="60" text-anchor="middle" font-size="20">Zero-salinity option OFF</text>',
                '<text x="1015" y="60" text-anchor="middle" font-size="20">Zero-salinity option ON</text>']
    for row, label in enumerate(labels):
        ytop, height = 100 + row * 270, 210
        samples = [v for panel in (left[row], right[row]) for curve in panel for v in curve if v is not None]
        low, high = (min(samples), max(samples)) if samples else (0.0, 1.0)
        margin = max((high - low) * 0.05, 1e-8)
        low, high = low - margin, high + margin
        if row >= 2 and samples and min(samples) >= 0:
            low = 0.0
        count = len(left[row])
        for index in range(count):
            number = (category if category is not None else index + 1) if row >= 4 else index + 1
            color = colors[(number - 1) % len(colors)] if row >= 4 else layer_color(index, count)
            y = ytop + index * height / max(1, count)
            elements.append(f'<rect x="1310" y="{y:.1f}" width="14" height="{height / max(1, count):.1f}" fill="{color}"/>')
            elements.append(f'<text x="1330" y="{y + height / max(1, count) * 0.8:.1f}" font-size="12">{number}</text>')
        for column, panel in enumerate((left[row], right[row])):
            x0, width = 115 + column * 640, 520
            elements.append(f'<path d="M{x0} {ytop} V{ytop + height} H{x0 + width}" fill="none" stroke="black"/>')
            for fraction in (0, 0.5, 1):
                y = ytop + height * (1 - fraction)
                elements.append(f'<text x="{x0 - 8}" y="{y + 5:.1f}" text-anchor="end" font-size="14">{low + fraction * (high - low):.3g}</text>')
                elements.append(f'<path d="M{x0} {y:.1f} H{x0 + width}" stroke="#dddddd"/>')
            for index, values in enumerate(panel):
                color = (colors[((category - 1) if category is not None else index) % len(colors)]
                         if row >= 4 else layer_color(index, len(panel)))
                segments, points = [], []
                for day, value in enumerate(values, 1):
                    if value is None:
                        if points:
                            segments.append(points)
                            points = []
                        continue
                    x = x0 + (day - 1) / max(1, days - 1) * width
                    y = ytop + height * (high - value) / (high - low)
                    points.append(f"{x:.2f},{y:.2f}")
                if points:
                    segments.append(points)
                for segment in segments:
                    if len(segment) == 1:
                        x, y = segment[0].split(",")
                        elements.append(f'<circle cx="{x}" cy="{y}" r="1.5" fill="{color}"/>')
                    else:
                        elements.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{color}" stroke-width="1.4"/>')
            if not any(value is not None for curve in panel for value in curve):
                elements.append(f'<text x="{x0 + width/2}" y="{ytop + height/2}" text-anchor="middle" font-size="16">No active samples</text>')
            for day in (1, 183, 365, 548, 730):
                if day <= days:
                    x = x0 + (day - 1) / max(1, days - 1) * width
                    elements.append(f'<text x="{x:.1f}" y="{ytop + height + 20}" text-anchor="middle" font-size="14">{day}</text>')
            elements.append(f'<text x="{x0 + width/2}" y="{ytop + height + 40}" text-anchor="middle" font-size="15">Elapsed day</text>')
        elements.append(f'<text x="20" y="{ytop + height/2}" transform="rotate(-90 20 {ytop + height/2})" text-anchor="middle" font-size="16">{html.escape(label)}</text>')
    elements.append('<text x="700" y="1760" text-anchor="middle" font-size="16">Layer colors progress from purple (top, 1) to red (bottom); thickness colors identify categories 1-5.</text>')
    elements.append('<text x="700" y="1785" text-anchor="middle" font-size="16">Gaps indicate inactive ice or absent snow; category-mean states are volume weighted.</text>')
    elements.append("</svg>")
    path.write_text("\n".join(elements) + "\n", encoding="utf-8")


def t13_layer_comparisons(runs: dict, output: Path, days: int = 730,
                          key: str = "T13") -> list[Path]:
    folder = output / f"{key}-layers"
    folder.mkdir()
    figures, summaries = [], []
    for layers in (7, 20):
        for forcing in ("control", "zero-sss"):
            off, on = (t13_profiles(runs[forcing, layers, mode], layers, days) for mode in ("off", "on"))
            for cell, category, view in [(2, None, "slab"), (3, None, "itd-mean"),
                                         *[(3, cat, f"itd-cat{cat}") for cat in range(1, 6)]]:
                if category is not None and not any(key[1:] == (cell, category) for key in off.keys() | on.keys()):
                    raise ValueError(f"missing T13 category {category}: {forcing}, {layers}")
                name = f"{key}-{forcing}-{layers}-{view}.svg"
                t13_plot(folder / name, t13_series(off, cell, category, layers, days),
                         t13_series(on, cell, category, layers, days),
                         f"{key} {forcing}, {layers} ice layers, {view}", days, category)
                figures.append(folder / name)
            for field in ("temperature_C", "sice_ppt", "phi"):
                differences = [abs(off[key]["ice"][layer][field] - on[key]["ice"][layer][field])
                               for key in off.keys() & on.keys() for layer in range(1, layers + 1)]
                if not differences:
                    raise ValueError(f"no common active T13 layer samples: {forcing}, {layers}")
                summaries.append({"forcing": forcing, "layers": layers, "field": field,
                                  "common_samples": len(differences), "max_abs_off_on": max(differences)})
    write_csv(folder / "summary.csv", summaries)
    return figures


def budget_checks(run: Path) -> list[dict[str, object]]:
    water, salt = [], []
    for row in csv_rows(run / "mushy_columns.csv"):
        dt = row["dt_s"]
        incoming = dt * (row["evap_kg_m2_s"] +
                    (row["fsnow_kg_m2_s"] + row["frain_kg_m2_s"]) * row["aice_initial_fraction"]
                    - row["fresh_ocean_kg_m2_s"]) + row["frazil_water_kg_m2"]
        if not row["l_mpond_fresh"]:
            incoming += row["rhofresh_kg_m3"] * row["fpond_m_per_step"]
        drainage = 1000 * sum(row["dpnd_" + key + "_m_per_step"]
                              for key in ("flush", "expon", "freebd", "initial", "ridge"))
        water.append(row["water_final_kg_m2"] - row["water_initial_kg_m2"] - incoming + drainage)
        salt.append(row["salt_final_kg_m2"] - row["salt_initial_kg_m2"]
                    + row["salt_ocean_kg_m2_s"] * dt - row["frazil_correction_salt_source_kg_m2"])
    heat = csv_rows(run / "mushy_energy.csv")
    # Recompute the local balance; do not merely trust the emitted residual.
    recomputed = [(row["final_energy_J_m2"] - row["initial_energy_J_m2"]
                   - row["input_energy_J_m2"]) / row["dt_s"] for row in heat]
    return [check("whole-cell water residual (kg/m2)", max(map(abs, water)), 1e-8),
            check("whole-cell salt residual (kg/m2)", max(map(abs, salt)), 1e-8),
            check("local heat residual (W/m2)", max(map(abs, recomputed)), 1.1e-3),
            check("local heat diagnostic identity (J/m2)", max(abs(
                row["final_energy_J_m2"] - row["initial_energy_J_m2"]
                - row["input_energy_J_m2"] - row["residual_J_m2"]) for row in heat), 1e-7)]


def binary_inverse(energy: float, salt: float) -> tuple[float, float, float | None]:
    """Toy binary mixture: e*=c*T+L*x, S=x*S_brine, T=-m*S_brine.

    Fractions here are *mass* fractions and enthalpy is J/kg.  This independent
    analytical reference is not Icepack's volumetric phase inversion.
    """
    latent, capacity, slope = 334000.0, 2100.0, 54.0
    if not math.isfinite(energy) or not math.isfinite(salt) or salt < 0:
        raise ValueError("binary reference requires finite enthalpy and nonnegative salt")
    if salt == 0:
        fraction = min(1.0, max(0.0, energy / latent))
    elif energy >= latent - capacity * slope * salt:
        fraction = 1.0
    else:
        root = math.sqrt(energy * energy + 4 * latent * capacity * slope * salt)
        # Rationalization avoids cancellation in the cold, dilute limit.
        fraction = (2 * capacity * slope * salt / (root - energy) if energy < 0
                    else (energy + root) / (2 * latent))
    temperature = (energy - latent * fraction) / capacity
    return fraction, temperature, salt / fraction if fraction > 0 else None


def binary_case(runner: Runner) -> tuple[list[dict[str, object]], list[Path], str]:
    folder = runner.output / "T1-binary"
    folder.mkdir()
    exact = [(-334000.0, 0.0, -334000 / 2100), (-1.0, 0.0, -1 / 2100),
             (0.0, 0.0, 0.0), (83500.0, 0.25, 0.0), (167000.0, 0.5, 0.0),
             (334000.0, 1.0, 0.0), (367400.0, 1.0, 33400 / 2100)]
    metrics = []
    for energy, target_fraction, target_temperature in exact:
        fraction, temperature, _ = binary_inverse(energy, 0.0)
        metrics += [check("exact binary liquid mass fraction", fraction - target_fraction, 2e-10),
                    check("exact binary temperature (C)", temperature - target_temperature, 2e-10)]
    rows = []
    for salt in (0.0, 1e-18, 1e-15, 1e-12, 1e-9, 1e-6, 1e-3, 5e-3):
        for energy in (-400000.0, -100000.0, -10000.0, -1.0, 0.0, 1.0,
                       10000.0, 100000.0, 334000.0, 400000.0):
            fraction, temperature, brine = binary_inverse(energy, salt)
            balance = energy - (2100 * temperature + 334000 * fraction)
            metrics.append(check("binary energy error / fixed tolerance",
                                 balance / max(1e-8, 1e-8 * abs(energy)), 1.0))
            if brine is not None:
                metrics.append(check("binary salt mass balance", salt - fraction * brine, 1e-12))
                if salt > 0 and 0 < fraction < 1:
                    metrics.append(check("binary liquidus residual (C)", temperature + 54 * brine, 1e-7))
            rows.append({"specific_enthalpy_J_kg": energy, "salt_mass_fraction": salt,
                         "liquid_mass_fraction": fraction, "temperature_C": temperature,
                         "energy_residual_J_kg": balance})
    write_csv(folder / "binary.csv", rows)
    write_csv(folder / "exact.csv", [{"specific_enthalpy_J_kg": energy,
              "expected_liquid_mass_fraction": fraction, "expected_temperature_C": temperature}
              for energy, fraction, temperature in exact])
    return metrics, [folder], "Seven exact freshwater cases and an 80-state binary-mixture closure grid; a toy reference, not compiled Icepack validation."


def core_case(runner: Runner, key: str) -> tuple[list[dict[str, object]], list[Path], str]:
    evidence: list[Path] = []
    metrics: list[dict[str, object]] = []
    note = ""
    if key == "T1":
        return binary_case(runner)
    elif key == "T2":
        for cap in (0.0, -0.1):
            run = runner.harness(f"T2-phase-cap-{cap}", phase_client(cap))
            checks, _ = phase_checks(run)
            metrics += checks
            evidence.append(run)
        regression = []
        for enabled in (False, True):
            settings = {"mushy_zero_salinity": ".false.", "sss_fixed": "34.0", "saltmax": "3.2"}
            run = runner.model("T2-" + ("reference" if enabled else "current"),
                               settings, reference=enabled)
            evidence.append(run)
            regression.append(run)
        metrics.append(check("default-off restart byte differences",
                             float(last_restart(regression[0]).read_bytes() != last_restart(regression[1]).read_bytes())))
        note = ("Eighteen compiled phase states and 53 ocean salinities under 0/-0.1 C caps. "
                "A separate saline default-off restart is compared with the recorded baseline git ref.")
    elif key == "T3":
        full = runner.model("T3-full")
        without_diagnostics = runner.model("T3-without-diagnostics", {"mushy_diagnostics": ".false."})
        half = runner.model("T3-half", {"npt": "24"})
        resumed = runner.model("T3-resumed", {"npt": "24"}, checkpoint=last_restart(half))
        metrics = layer_checks(full, fresh=True)
        metrics.append(check("restart continuation byte differences",
                             float(last_restart(full).read_bytes() != last_restart(resumed).read_bytes())))
        metrics.append(check("optional diagnostics restart byte differences", float(
            last_restart(full).read_bytes() != last_restart(without_diagnostics).read_bytes())))
        evidence += [full, half, resumed, without_diagnostics]
    elif key in ("T4", "T6", "T8"):
        if key == "T4":
            specs = {method: {"congel_freeze": f"'{method}'", "saltmax": "3.2",
                              "npt": "48"} for method in ("one-step", "two-step")}
        elif key == "T6":
            specs = {flag: {"istep0": "4344", "npt": "360", "atm_data_type": "'clim'",
                            "saltmax": "3.2", "sss_fixed": "5.0", "tr_pond_topo": ".true.",
                            "congel_freeze": "'one-step'",
                            "l_mpond_fresh": flag} for flag in (".false.", ".true.")}
        else:
            specs = {
                "zero": {"saltmax": "1e-12", "conserv_check": ".true."},
                "tiny": {"saltmax": "1e-12", "sss_fixed": "1e-12", "conserv_check": ".true."},
                **{method: {"congel_freeze": f"'{method}'", "saltmax": "3.2", "npt": "48",
                             "conserv_check": ".true."} for method in ("one-step", "two-step")},
                **{flag: {"istep0": "4344", "npt": "360", "atm_data_type": "'clim'",
                           "saltmax": "3.2", "sss_fixed": "5.0", "tr_pond_topo": ".true.",
                           "congel_freeze": "'one-step'",
                           "conserv_check": ".true.", "l_mpond_fresh": flag}
                   for flag in (".false.", ".true.")},
            }
        for index, (label, values) in enumerate(specs.items()):
            run = runner.model(f"{key}-{index + 1}", values)
            evidence.append(run)
            metrics += layer_checks(run)
            if key == "T8":
                metrics += budget_checks(run)
            if key == "T4":
                columns = csv_rows(run / "mushy_columns.csv")
                metrics.append(check("nonvacuous basal growth", float(
                    max(row["basal_congelation_m"] for row in columns) <= 0)))
                profiles = final_profiles(run)
                basal = [rows[-1]["sice_ppt"] for rows in profiles.values()]
                metrics.append(check("basal salinity below legacy 0.1 ppt floor",
                                     float(min(basal) >= 0.1)))
            if key == "T6":
                columns = csv_rows(run / "mushy_columns.csv")
                metrics.append(check("pond feedback identity (kg/m2/s)", max(abs(
                    row["fresh_after_kg_m2_s"] - row["fresh_before_kg_m2_s"] +
                    (row["fpond_m_per_step"] * row["rhofresh_kg_m3"] / row["dt_s"]
                     if label == ".true." else 0)) for row in columns), 1e-12))
        if key == "T6":
            activity = max(abs(row["fpond_m_per_step"]) for path in evidence
                           for row in csv_rows(path / "mushy_columns.csv"))
            metrics.append(check("nonvacuous pond activity", float(activity == 0)))
        if key == "T8":
            zero, tiny = [{(row["step"], row["cell"]): row for row in csv_rows(path / "mushy_columns.csv")}
                          for path in evidence[:2]]
            if set(zero) != set(tiny):
                raise ValueError("incomplete zero/tiny budget pair")
            metrics += zero_sss_frazil_checks(list(zero.values()))
            for field, tolerance in (("frazil_water_kg_m2", 1e-10),
                    ("fresh_ocean_kg_m2_s", 1e-12), ("heat_ocean_W_m2", 1e-9),
                    ("energy_final_J_m2", 1e-5)):
                metrics.append(check("zero/tiny " + field,
                    max(abs(zero[key][field] - tiny[key][field]) for key in zero), tolerance))
        note = "Heat balance is local thermodynamics, not full coupled/pond energy or salt closure."
    elif key == "T7":
        run = runner.harness("T7-connectivity", CONNECTIVITY_CLIENT)
        evidence.append(run)
        for row in csv_rows(run / "connectivity.csv"):
            metrics += [check("phase target", row["phi"] - row["target_phi"], 1e-12),
                        check("Golden permeability (m2)",
                              row["permeability"] - 3e-8 * max(row["phi"] - 0.05, 0) ** 3, 1e-22)]
            metrics.append(check("flushing connectivity",
                           float((row["velocity"] > 0) != (row["target_phi"] > 0.05))))
            for flux in ("water_flux", "enthalpy_flux"):
                metrics.append(check(f"flushing {flux} connectivity",
                               float((row[flux] > 0) != (row["target_phi"] > 0.05))))
            metrics.append(check("freshwater flushing salt flux", row["salt_flux"]))
    elif key == "T10-brine":
        phase = runner.harness("T10-brine-constants", phase_client())
        _, constants = phase_checks(phase)
        integrated_runs = []
        for enabled in (False, True):
            for tiny in (False, True):
                integrated_run = runner.model(f"T10-brine-column-{enabled}-{tiny}",
                    {"mushy_diagfreq": "24", "tr_brine": ".true.",
                     "mushy_zero_salinity": ".true." if enabled else ".false.",
                     "sss_fixed": "1e-12" if tiny else "0.0"}, bgc=True)
                metrics += layer_checks(integrated_run)
                integrated_runs.append(integrated_run)
        integrated = integrated_runs[2]
        groups: dict[tuple[float, float, float], list[dict[str, float]]] = {}
        for row in csv_rows(integrated / "mushy_layers.csv"):
            groups.setdefault((row["step"], row["cell"], row["category"]), []).append(row)
        profiles = []
        for rows in groups.values():
            rows.sort(key=lambda row: row["layer"])
            if len(rows) != 7:
                raise ValueError("integrated brine replay requires complete seven-layer profiles")
            profiles.append(" ".join(str(row["q_J_m3"]) for row in rows) + " " +
                            " ".join(str(row["sice_ppt"]) for row in rows))
        replay = str(len(profiles)) + "\n" + "\n".join(profiles) + "\n"
        run = runner.harness("T10-brine-replay", BRINE_CLIENT, files={"profiles.in": replay})
        records = csv_rows(run / "brine.csv")
        metrics.append(check("linear/constant enthalpy remap (J/m3)", max(abs(
            row["q"] - row["expected_q"]) for row in records if row["profile"] <= 7), 1e-6))
        for field in ("phi_before", "phi_after"):
            metrics.append(check(field + " freshwater phase", max(abs(row[field] -
                freshwater_state(row["q"], constants)[1]) for row in records), 1e-12))
        metrics += [check("freshwater brine salinity (ppt)", max(abs(row["brine_salinity"]) for row in records)),
                    check("freshwater brine temperature (C)", max(abs(row["temperature"] -
                        freshwater_state(row["q"], constants)[0]) for row in records), 1e-10)]
        tiny = [row for row in records if row["profile"] == 3]
        metrics.append(check("tiny liquid fraction/permeability remain positive",
            float(any(row["phi_before"] <= 0 or row["phi_after"] <= 0 or row["permeability"] <= 0 for row in tiny))))
        solid = [row for row in records if row["profile"] in (1, 2)]
        metrics.append(check("controlled solid phase/permeability exactly zero",
            max(max(abs(row[field]) for field in ("phi_before", "phi_after", "permeability")) for row in solid)))
        for index in {row["profile"] for row in records}:
            rows = [row for row in records if row["profile"] == index]
            phis = [row["phi_after"] for row in rows]
            if len(rows) != 3 or {row["layer"] for row in rows} != {1, 2, 3}:
                raise ValueError(f"incomplete brine profile: {index}")
            expected = 0.0 if min(phis) == 0 else len(phis) / sum(1 / (3e-8 * phi**3) for phi in phis)
            for row in rows:
                actual = row["permeability"]
                if expected == 0:
                    metrics.append(check("solid brine permeability (m2)", actual))
                else:
                    metrics.append(check("harmonic brine permeability relative error",
                                         (actual - expected) / expected, 2e-12))
        evidence += [phase, *integrated_runs, run]
        note = "Seven controlled profiles plus public-API replay of integrated freshwater profiles; no blanket-zero-porosity expectation."
    elif key == "T10-BGC":
        run = runner.harness("T10-BGC-initializer", BGC_CLIENT, bgc=True)
        log = (run / "harness.log").read_text()
        metrics = [check(marker, float(marker not in log))
                   for marker in ("BGC_ZERO_OK", "BGC_TINY_OK", "BGC_GUARD_OK")]
        evidence.append(run)
        note = "Active-tracer initializer only; this does not establish subsequent tracer evolution."
    else:
        raise ValueError(f"not a core case: {key}")
    return metrics, evidence, note


# Pinned numerical settings: one example of a current Icepack configuration
# used in Earth system models.  These are explicit rather than inherited from
# changing driver defaults.  Synthetic tests still use the simpler FRESH setup.
PINNED = {
    "days_per_year": "365", "use_leap_years": ".false.", "year_init": "2015",
    "istep0": "0", "dt": "1800.0", "ndtd": "1", "npt": "35040", "diagfreq": "48",
    "dumpfreq": "'d'", "dump_last": ".true.", "restart": ".false.",
    "restart_format": "'bin'", "history_format": "'none'", "cpl_bgc": ".false.",
    "conserv_check": ".false.", "hi_init_slab": "2.0", "hsno_init_slab": "0.0",
    "hbar_init_itd": "3.0", "hsno_init_itd": "0.25", "sst_init": "-1.8",
    "kcatbound": "0", "kitd": "1", "ktherm": "2", "mushy_zero_salinity": ".true.",
    "saltmax": "3.2", "conduct": "'bubbly'", "hi_min": "0.01", "ksno": "0.3",
    "a_rapid_mode": "0.5e-3", "Rac_rapid_mode": "10.0", "aspect_rapid_mode": "1.0",
    "dSdt_slow_mode": "-5.0e-8", "phi_c_slow_mode": "0.05", "phi_i_mushy": "0.85",
    "Tliquidus_max": "-0.1", "floediam": "300.0", "hfrazilmin": "0.05",
    "tscale_pnd_drain": "0.5", "shortwave": "'dEdd_snicar_ad'", "albedo_type": "'ccsm3'",
    "albicev": "0.78", "albicei": "0.36", "albsnowv": "0.98", "albsnowi": "0.70",
    "ahmax": "0.3", "R_ice": "0.0", "R_pnd": "0.0", "R_snw": "1.5",
    "dT_mlt": "1.5", "rsnw_mlt": "1500.0", "kalg": "0.6",
    "sw_redist": ".false.", "sw_frac": "0.9", "sw_dtemp": "0.02", "snw_ssp_table": "'snicar'",
    "hp1": "0.01", "hs0": "0.0", "hs1": "0.03", "dpscale": "1.0e-3",
    "frzpnd": "'hlid'", "rfracmin": "1.0", "rfracmax": "1.0", "pndaspect": "0.8",
    "apnd_sl": "0.27", "snwredist": "'ITDrdg'", "snwgrain": ".true.",
    "use_smliq_pnd": ".true.", "rsnw_fall": "54.526", "rsnw_tmax": "2800.0",
    "rhosnew": "100.0", "rhosmin": "100.0", "rhosmax": "450.0", "windmin": "10.0",
    "drhosdwind": "27.3", "snwlvlfac": "0.3", "snw_growth_wet": "4.22e5",
    "drsnw_min": "0.0", "snwliq_max": "0.033", "snw_aging_table": "'test'",
    "formdrag": ".false.", "atmbndy": "'similarity'", "calc_strair": ".true.",
    "calc_Tsfc": ".true.", "highfreq": ".true.", "natmiter": "5", "ustar_min": "0.0005",
    "calc_dragio": ".false.", "emissivity": "0.985", "fbot_xfer_type": "'constant'",
    "cpl_frazil": "'external'", "update_ocn_f": ".true.", "l_mpond_fresh": ".false.",
    "congel_freeze": "'two-step'", "tfrz_option": "'mushy'", "ice_ref_salinity": "4.0",
    "saltflux_option": "'constant'", "oceanmixed_ice": ".false.", "restore_ocn": ".false.",
    "trestore": "90", "sss_fixed": "34.0", "qdp_fixed": "0.0", "hmix_fixed": "20.0",
    "precip_units": "'mks'", "default_season": "'winter'", "atm_data_type": "'NICE'",
    "ocn_data_type": "'NICE'", "bgc_data_type": "'default'", "lateral_flux_type": "'uniform_ice'",
    "fyear_init": "2015", "ycycle": "1", "precalc_forc": ".false.",
    "semi_implicit_Tsfc": ".false.", "vapor_flux_correction": ".false.",
    "tr_iage": ".false.", "tr_FY": ".false.", "tr_lvl": ".true.",
    "tr_pond_lvl": ".false.", "tr_pond_topo": ".false.", "tr_pond_sealvl": ".true.",
    "tr_snow": ".true.", "tr_aero": ".false.", "tr_fsd": ".false.",
    "tr_iso": ".false.", "tr_brine": ".false.", "tr_zaero": ".false.",
    "wave_spec_type": "'none'", "wave_height_type": "'none'", "nfreq": "0",
    "kstrength": "1", "krdg_partic": "1", "krdg_redist": "1",
    "mushy_profilefreq": "48",
}
ANNUAL = {
    "fresh-reference": {},
    "positive-saltmax": {"saltmax": "3.2"},
    "minimum-thickness": {"hi_min": "0.02"},
    "phase-and-new-ice": {"Tliquidus_max": "-0.05", "phi_i_mushy": "0.75",
                          "hfrazilmin": "0.1", "floediam": "500.0"},
    "conductivity": {"conduct": "'MU71'", "ksno": "0.25"},
    "rapid-drainage": {"a_rapid_mode": "0.8e-3", "Rac_rapid_mode": "12.0",
                       "aspect_rapid_mode": "1.5"},
    "slow-drainage": {"dSdt_slow_mode": "-1.0e-7", "phi_c_slow_mode": "0.1",
                      "tscale_pnd_drain": "5.0"},
    "salinity-boundary": {"saltmax": "3.2", "ice_ref_salinity": "4.0", "sss_fixed": "34.0"},
    "frazil-coupling": {"cpl_frazil": "'external'", "update_ocn_f": ".true."},
    "one-step": {"congel_freeze": "'one-step'"},
    "delta-itd": {"kcatbound": "0", "kitd": "0"},
}
SEASONS = (("winter", 90), ("spring", 91), ("summer", 92), ("winter", 92))


def salinity_profile(saltmax: float, layers: int) -> list[float]:
    return [max(0.1, saltmax * 0.5 * (1 - math.cos(math.pi *
            ((index + 0.5) / layers) ** (0.407 / (0.573 + (index + 0.5) / layers)))))
            for index in range(layers)]


def calibrated_salinity(layers: int) -> tuple[float, float]:
    target = sum(salinity_profile(3.2, 7)) / 7
    low, high = 0.0, 20.0
    for _ in range(100):
        middle = (low + high) / 2
        if sum(salinity_profile(middle, layers)) / layers < target:
            low = middle
        else:
            high = middle
    value = (low + high) / 2
    return value, sum(salinity_profile(value, layers)) / layers - target


def interpolate(values: list[float], x: float) -> float:
    position = min(max(x * len(values) - 0.5, 0), len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (position - lower) * (values[upper] - values[lower])


def final_profiles(run: Path) -> dict[tuple[int, int], list[dict[str, float]]]:
    rows = csv_rows(run / "mushy_layers.csv")
    final_step = max(row["step"] for row in rows)
    profiles: dict[tuple[int, int], list[dict[str, float]]] = {}
    for row in rows:
        if row["step"] == final_step:
            profiles.setdefault((int(row["cell"]), int(row["category"])), []).append(row)
    for values in profiles.values():
        values.sort(key=lambda row: row["layer"])
    return profiles


def compare_t5(low: Path, high: Path) -> list[dict[str, object]]:
    coarse, fine = final_profiles(low), final_profiles(high)
    common = {key for key in set(coarse) & set(fine) if key[0] in (2, 3)}
    if not common:
        raise ValueError("T5 has no common active final categories")
    metrics = []
    lower, upper = len(next(iter(coarse.values()))), len(next(iter(fine.values())))
    prefix = f"{lower}-to-{upper}-layer "
    for field, tolerance in (("temperature_C", 0.1), ("phi", 0.01), ("sice_ppt", 0.05)):
        delta = max(abs(interpolate([row[field] for row in coarse[key]], index / 100) -
                        interpolate([row[field] for row in fine[key]], index / 100))
                    for key in common for index in range(101))
        metrics.append(check(prefix + field, delta, tolerance))
    a = {row["cell"]: row["basal_congelation_m"]
         for row in csv_rows(low / "mushy_columns.csv") if row["step"] == 48 and row["cell"] in (2, 3)}
    b = {row["cell"]: row["basal_congelation_m"]
         for row in csv_rows(high / "mushy_columns.csv") if row["step"] == 48 and row["cell"] in (2, 3)}
    if set(a) != set(b) or len(a) != 2:
        raise ValueError("incomplete T5 growth comparison")
    metrics.append(check(prefix + "basal growth difference (%)",
                         max(100 * abs(a[cell] - b[cell]) / max(abs(b[cell]), 1e-12) for cell in a), 2.0))
    return metrics


def forcing_pair(runner: Runner, key: str) -> tuple[Path, Path, Path]:
    source = runner.data["forcing"] / "Icepack_data" / "forcing" / "NICE_2015"
    original = source / "oceanmixed_daily_3.txt"
    atmosphere = source / "NICE_atm_forcing_05092018.txt"
    folder = runner.output / f"{key}-forcing"
    folder.mkdir()
    text = original.read_text()
    matches = list(NUMBER.finditer(text))
    if len(matches) < 8 * 365 or len(matches) % 365:
        raise ValueError("unexpected NICE ocean forcing shape")
    replacements = []
    for index, match in enumerate(matches[365:730], start=1):
        distance = min(min(abs(index - center), 365 - abs(index - center))
                       for center in (31, 92, 153, 214, 275, 336))
        value = float(match[0].replace("D", "E").replace("d", "e"))
        replacements.append((match.span(), value * (1 - math.exp(-0.5 * (distance / 3) ** 2))))
    updated = text
    for (start, end), value in reversed(replacements):
        updated = updated[:start] + f"{value:.14f}" + updated[end:]
    control, treatment = folder / "control.txt", folder / "zero-sss.txt"
    control.write_text(text)
    treatment.write_text(updated)
    before = [float(match[0].replace("D", "E").replace("d", "e")) for match in matches]
    after = [float(match[0].replace("D", "E").replace("d", "e")) for match in NUMBER.finditer(updated)]
    if (len(before) != len(after) or any(before[index] != after[index]
            for index in range(len(before)) if not 365 <= index < 730)
            or any(after[365 + day - 1] != 0 for day in (31, 92, 153, 214, 275, 336))):
        raise ValueError("forcing treatment changed a non-SSS field or lost exact-zero centers")
    write_json(folder / "provenance.json", {"source": DATASETS["forcing"], "control_sha256": sha256(control),
               "treatment_sha256": sha256(treatment), "non_sss_identical": True,
               "sigma_days": 3, "centers_1based": [31, 92, 153, 214, 275, 336]})
    return atmosphere, control, treatment


def compare_t12(coarse: Path, fine: Path) -> list[dict[str, object]]:
    a = {(row["step"], row["cell"]): row for row in csv_rows(coarse / "mushy_columns.csv")
         if row["cell"] in (2, 3)}
    b = {(row["step"], row["cell"]): row for row in csv_rows(fine / "mushy_columns.csv")
         if row["cell"] in (2, 3)}
    if set(a) != set(b) or len(a) != 2 * 35040:
        raise ValueError("T12 requires matching complete two-year per-step series")
    metrics = []
    for cell in (2, 3):
        keys = [key for key in a if key[1] == cell]
        active = [key for key in keys if a[key]["aice_final_fraction"] > 1e-8 and
                  b[key]["aice_final_fraction"] > 1e-8]
        if not active:
            raise ValueError("T12 has no common ice-covered timesteps")
        temp = max(abs(a[key]["surface_temperature_C"] - b[key]["surface_temperature_C"]) for key in active)
        delta = sum(abs(a[key]["salt_ocean_kg_m2_s"] - b[key]["salt_ocean_kg_m2_s"])
                    * b[key]["dt_s"] for key in keys)
        reference = sum(abs(b[key]["salt_ocean_kg_m2_s"]) * b[key]["dt_s"] for key in keys)
        metrics += [check(f"cell {cell} surface temperature difference (C)", temp, 0.1),
                    check(f"cell {cell} salt flux " + ("relative L1" if reference > 1e-6 else "absolute L1 (kg/m2)"),
                          delta / reference if reference > 1e-6 else delta,
                          0.02 if reference > 1e-6 else 1e-6)]
    return metrics


def freezing_boundary_checks(run: Path, freshening: bool,
                             steps: int = 35040) -> list[dict[str, object]]:
    """Check actual boundary states at every step, including exact-zero events."""
    rows = csv_rows(run / "mushy_columns.csv")
    metrics = []
    for cell in (2, 3):
        samples = [row for row in rows if row["cell"] == cell]
        if [row["step"] for row in samples] != list(range(1, steps + 1)):
            raise ValueError(f"incomplete paired-forcing boundary series: {run}, cell {cell}")
        prefix = f"cell {cell} "
        metrics.append(check(prefix + "actual SST minus freezing temperature (C)",
                             max(abs(row["sst_C"] - row["freezing_temperature_C"])
                                 for row in samples), 1e-12))
        zeros = [row for row in samples if row["sss_ppt"] == 0]
        metrics.append(check(prefix + "exact-zero salinity event count",
                             len(zeros) - (12 if freshening else 0)))
        if freshening and zeros:
            metrics.append(check(prefix + "actual SST at zero salinity (C)",
                                 max(abs(row["sst_C"]) for row in zeros)))
            metrics.append(check(prefix + "freezing temperature at zero salinity (C)",
                                 max(abs(row["freezing_temperature_C"]) for row in zeros)))
    return metrics


OBSERVATION_CLIENT = r"""
program observation_client
 use icepack_intfc, dbl_kind => icepack_dbl_kind
 implicit none
 integer :: u,v,n,i,k
 real(dbl_kind) :: z,top,bot,thickness,overlap,s,q,to,po,t,phi,si,qi
 real(dbl_kind) :: a(8),b(8),salt(8),temp(8),brine(8),energy(8)
 call icepack_configure()
 call check_warnings()
 call icepack_init_parameters(ktherm_in=2,mushy_zero_salinity_in=.false.)
 call check_warnings()
 open(newunit=u,file='sections.in',status='old')
 read(u,*) n,z
 if(n/=8) error stop 'unexpected observation profile shape'
 do i=1,n
   read(u,*) a(i),b(i),salt(i),temp(i),brine(i)
   energy(i)=icepack_enthalpy_mush(zTin=temp(i),zSin=salt(i))
   call check_warnings()
 enddo
 close(u)
 open(newunit=v,file='adapted.csv',status='new')
 write(v,'(a)') 'depth_m,observed_salinity,adapted_salinity,enthalpy,observed_temperature,temperature,phi,observed_brine'
 thickness=z/7.d0
 do k=1,7
   top=(k-1)*thickness
   bot=k*thickness
   si=0.d0
   qi=0.d0
   to=0.d0
   po=0.d0
   do i=1,n
     overlap=max(0.d0,min(bot,b(i))-max(top,a(i)))
     si=si+overlap*salt(i)
     qi=qi+overlap*energy(i)
     to=to+overlap*temp(i)
     po=po+overlap*brine(i)
   enddo
   s=si/thickness
   q=qi/thickness
   call icepack_mushy_state(zqin=q,zSin=s,zTin=t,phi=phi)
   call check_warnings()
   write(v,'(*(g0,:,","))') (top+bot)/2.d0,s,s,q,to/thickness,t,phi,po/thickness
 enddo
 close(v)
 open(newunit=v,file='source-integrals.csv',status='new')
 write(v,'(a)') 'salt,enthalpy,depth'
 write(v,'(*(g0,:,","))') sum((b-a)*salt),sum((b-a)*energy),z
 close(v)
 contains
 subroutine check_warnings()
   call icepack_warnings_flush(6)
   if(icepack_warnings_aborted()) error stop 'observation state interface failed'
 end subroutine
end program
"""


def observation_case(runner: Runner) -> tuple[list[dict[str, object]], list[Path], str]:
    lines = runner.data["profiles"].read_text(encoding="utf-8-sig").splitlines()
    header = next(index for index, line in enumerate(lines) if line.startswith("Event\tDate/Time\t"))
    reader = csv.DictReader(lines[header:], delimiter="\t")
    def column(prefix: str) -> str:
        matches = [name for name in reader.fieldnames or [] if name.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(f"ambiguous/missing observation column: {prefix}")
        return matches[0]
    fields = [column(prefix) for prefix in ("Event", "Date/Time", "Sample ID",
              "Depth ice/snow top", "Depth ice/snow bot", "Sea ice sal", "t [", "Vol brine")]
    sections = []
    for row in reader:
        if (row[fields[0]] == "PS122/1_5-3" and row[fields[1]] == "2019-10-27T23:46:00"
                and re.sub(r"\s+\d+$", "", row[fields[2]].strip()) == "PS122-1_5-FYI"):
            sections.append([-float(row[fields[3]]), -float(row[fields[4]]),
                             float(row[fields[5]]), float(row[fields[6]]), float(row[fields[7]]) / 100])
    sections.sort()
    if len(sections) != 8 or abs(sections[0][0]) > 1e-12 or abs(sections[-1][1] - 0.420) > 1e-12:
        raise ValueError("pinned observation event/profile has unexpected coverage")
    for index, section in enumerate(sections):
        if (section[1] <= section[0] or section[2] < 0 or section[3] > -0.1 or
                (index and abs(sections[index - 1][1] - section[0]) > 1e-12)):
            raise ValueError("invalid/gapped observation sections")
    contents = "8 0.420\n" + "\n".join(" ".join(map(str, section)) for section in sections) + "\n"
    run = runner.harness("T9-observation", OBSERVATION_CLIENT, files={"sections.in": contents})
    rows = csv_rows(run / "adapted.csv")
    source = csv_rows(run / "source-integrals.csv")[0]
    metrics = [check("conservative salt integral (ppt m)", sum(row["adapted_salinity"] for row in rows)
                     * source["depth"] / 7 - source["salt"], 1e-10),
               check("conservative enthalpy integral (J/m2)", sum(row["enthalpy"] for row in rows)
                     * source["depth"] / 7 - source["enthalpy"], 1e-6)]
    return metrics, [run], ("Static seven-layer adaptation, not a transient hindcast. Practical salinity is "
                           "treated numerically as ppt (1:1 approximation); temperature/brine differences "
                           "are descriptive, not fitted acceptance thresholds.")


def annual_case(runner: Runner) -> tuple[list[dict[str, object]], list[Path], str]:
    metrics = []
    evidence = []
    summary = []
    for enabled in (False, True):
        checkpoint = None
        flag = ".true." if enabled else ".false."
        for segment, (season, days) in enumerate(SEASONS):
            run = runner.model(f"T10-annual-{enabled}-{segment+1}",
                {"npt": str(days * 24), "mushy_zero_salinity": flag, "mushy_diagfreq": "24",
                 "default_season": f"'{season}'"}, checkpoint=checkpoint)
            checkpoint = last_restart(run)
            evidence.append(run)
            metrics += layer_checks(run, fresh=enabled)
        for label, overrides in ANNUAL.items():
            run = runner.model(f"T10-matrix-{label}-{enabled}", {**overrides, "npt": "24",
                "mushy_zero_salinity": flag, "mushy_diagfreq": "24", "default_season": "'winter'"})
            evidence.append(run)
            metrics += layer_checks(run, fresh=enabled and "saltmax" not in overrides and "sss_fixed" not in overrides)
    for run in evidence:
        rows = csv_rows(run / "mushy_layers.csv")
        summary.append({"run": run.name, "samples": len(rows),
                        "min_salinity_ppt": min(row["sice_ppt"] for row in rows),
                        "max_salinity_ppt": max(row["sice_ppt"] for row in rows),
                        "max_liquid_fraction": max(row["phi"] for row in rows)})
    folder = runner.output / "T10-summary"
    folder.mkdir()
    write_csv(folder / "summary.csv", summary)
    evidence.append(folder)
    return metrics, evidence, ("Paired flag off/on 365-day synthetic seasonal integrations plus "
        "eleven paired one-day namelist families; not an observation-driven hindcast.")


def extended_case(runner: Runner, key: str) -> tuple[list[dict[str, object]], list[Path], str]:
    metrics: list[dict[str, object]] = []
    evidence: list[Path] = []
    if key == "T9":
        return observation_case(runner)
    if key == "T10":
        return annual_case(runner)
    if key == "T5":
        pair_rows = []
        layer_counts = (4, 7, 10, 14, 20, 28)
        for scenario in ("fresh", "one-step", "two-step"):
            runs = {}
            for layers in layer_counts:
                saltmax, error = calibrated_salinity(layers)
                values = {"npt": "48", "saltmax": "0.0" if scenario == "fresh" else repr(saltmax)}
                if scenario != "fresh":
                    values["congel_freeze"] = f"'{scenario}'"
                run = runner.model(f"T5-{scenario}-{layers}", values, layers)
                runs[layers] = run
                evidence.append(run)
                metrics += layer_checks(run)
                metrics.append(check("initial mean salinity calibration (ppt)", error, 1e-10))
            for lower, upper in zip(layer_counts, layer_counts[1:]):
                paired = compare_t5(runs[lower], runs[upper])
                pair_rows += [{"case": scenario, "coarse_layers": lower,
                               "fine_layers": upper, **metric} for metric in paired]
                if (lower, upper) == (10, 14):
                    metrics += paired
        folder = runner.output / "T5-summary"
        folder.mkdir()
        write_csv(folder / "summary.csv", pair_rows)
        evidence.append(folder)
        note = ("Fixed 10-to-14-layer acceptance limits: 0.1 C, 0.01 liquid fraction, "
                "0.05 ppt and 2% basal growth. Other adjacent pairs, including 20-to-28, "
                "are follow-up measurements under the same unchanged limits.")
    elif key in ("T10", "T10-cost"):
        specs = ANNUAL if key == "T10" else {"off": {"mushy_zero_salinity": ".false."},
                                            "on": {"mushy_zero_salinity": ".true."}}
        cost_rows = []
        for layers in ((7,) if key == "T10" else (7, 20)):
            for label, settings in specs.items():
                checkpoint = None
                for segment, (season, days) in enumerate(SEASONS):
                    values = dict(settings)
                    if key == "T10-cost":
                        values = {**PINNED, **settings, "atm_data_type": "'default'",
                                  "ocn_data_type": "'default'", "oceanmixed_ice": ".false.",
                                  "saltmax": "0.0", "sss_fixed": "0.0", "ice_ref_salinity": "0.0"}
                    values.update(npt=str(days * (48 if key == "T10-cost" else 24)),
                                  default_season=f"'{season}'",
                                  mushy_diagfreq="48" if key == "T10-cost" else "24")
                    run = runner.model(f"{key}-{label}-{layers}-{segment+1}", values, layers,
                                       checkpoint=checkpoint, pinned=key == "T10-cost")
                    checkpoint = last_restart(run)
                    evidence.append(run)
                    metrics += layer_checks(run, fresh=key == "T10" and
                                             "saltmax" not in settings and "sss_fixed" not in settings)
                    if key == "T10-cost":
                        timing = json.loads((run / "timing.json").read_text())
                        cost_rows.append({"layers": layers, "mode": label, "days": days,
                                          "model_wall_s": timing["wall_seconds"],
                                          "model_cpu_s": timing["cpu_seconds"],
                                          "restart_bytes": sum(path.stat().st_size for path in (run / "restart").glob("iced.*"))})
        if key == "T10-cost":
            folder = runner.output / "T10-cost-summary"
            folder.mkdir()
            totals = []
            for layers in (7, 20):
                for mode in ("off", "on"):
                    subset = [row for row in cost_rows if row["layers"] == layers and row["mode"] == mode]
                    totals.append({"layers": layers, "mode": mode,
                        **{field: sum(row[field] for row in subset)
                           for field in ("days", "model_wall_s", "model_cpu_s", "restart_bytes")}})
            write_csv(folder / "summary.csv", totals)
            evidence.append(folder)
        note = ("365-day segmented synthetic seasonal experiment. Cost includes four-cell debug startup "
                "and daily diagnostic/restart I/O, not production throughput or a performance threshold.")
    elif key in ("T11", "T12", "T13", "T14"):
        atmosphere, control, treatment = forcing_pair(runner, key)
        evidence.append(control.parent)
        refinement = key == "T12"
        counts = (7, 10, 14, 20, 28) if refinement else (7, 20)
        runs: dict[tuple[str, int, str], Path] = {}
        for layers in counts:
            saltmax, error = calibrated_salinity(layers) if key in ("T12", "T14") else (3.2, 0.0)
            if refinement:
                metrics.append(check("initial mean salinity calibration (ppt)", error, 1e-10))
            for label, ocean in (("control", control), ("zero-sss", treatment)):
                for mode in (("off", "on") if key in ("T13", "T14") else ("on",)):
                    values = {**PINNED, "saltmax": repr(saltmax),
                              "mushy_zero_salinity": ".false." if mode == "off" else ".true."}
                    if key == "T14":
                        values["sst_at_freezing"] = ".true."
                    run = runner.model(f"{key}-{label}-{layers}-{mode}", values, layers,
                                       pinned=True, forcing=(atmosphere, ocean))
                    runs[label, layers, mode] = run
                    evidence.append(run)
                    metrics += layer_checks(run)
                    if key == "T14":
                        metrics += freezing_boundary_checks(run, label == "zero-sss")
        if key in ("T13", "T14"):
            t13_layer_comparisons(runs, runner.output, key=key)
            evidence.append(runner.output / f"{key}-layers")
        if refinement:
            pair_rows = []
            adjacent_pass = []
            for lower, upper in zip(counts, counts[1:]):
                paired = []
                for label in ("control", "zero-sss"):
                    case_metrics = compare_t12(runs[label, lower, "on"], runs[label, upper, "on"])
                    paired += case_metrics
                    pair_rows += [{"coarse_layers": lower, "fine_layers": upper,
                                   "case": label, **metric} for metric in case_metrics]
                adjacent_pass.append(all(row["passed"] for row in paired))
                write_json(control.parent / f"T12-pair-{lower}-{upper}.json", paired)
            metrics.append(check("two successive adjacent refinement pairs",
                           float(not any(a and b for a, b in zip(adjacent_pass, adjacent_pass[1:])))))
            write_csv(control.parent / "summary.csv", pair_rows)
        note = ("Two repeated 365-day prescribed-forcing years, not a hindcast. Six smooth salinity "
                "notches attain exact zero; non-salinity ocean input fields are unchanged. "
                "T12 requires both metrics, both control/treatment cases and both target cells "
                "on two successive adjacent refinement pairs; tolerances remain 0.1 C and 2% relative "
                "salt-flux L1 (1e-6 kg/m2 absolute below the 1e-6 reference-integral floor).")
        if key in ("T13", "T14"):
            note += (" Daily paired layer plots compare both flags at 7 and 20 ice layers, "
                     "for control and zero-SSS forcing, the slab and full-ITD cells, and "
                     "each full-ITD category. Missing ice or snow is shown as a gap.")
        if key == "T14":
            note = ("Companion to T11-T13: the same atmosphere and control/freshening salinities, "
                    "but actual SST is prescribed at Icepack's salinity-dependent freezing point "
                    "at every timestep (sst_at_freezing=T; mixed-layer evolution and restoring off). "
                    "Both actual SST and SSS must reach exact zero in 12 samples per target cell. "
                    "Both flags run at 7 and 20 layers with mean-salinity-calibrated initial ice. "
                    "Acceptance checks actual SST tracking, simultaneous exact-zero boundary events, "
                    "and valid layer states; it does not require layer-refinement convergence. "
                    "Daily off/on layer comparisons are descriptive, with no equivalence threshold. "
                    "This prescribed boundary is not a coupled ocean heat-budget calculation.")
    else:
        raise ValueError(f"unknown extended case: {key}")
    if key in ("T11", "T13", "T14"):
        folder = runner.output / f"{key}-summary"
        folder.mkdir()
        rows = []
        for run in evidence:
            if (run / "mushy_columns.csv").is_file():
                columns = csv_rows(run / "mushy_columns.csv")
                final = max(row["step"] for row in columns)
                for cell in (2, 3):
                    series = [row for row in columns if row["cell"] == cell]
                    state = next(row for row in series if row["step"] == final)
                    rows.append({"run": run.name, "cell": cell, "final_ice_volume_m": state["ice_volume_m"],
                                 "min_sss_ppt": min(row["sss_ppt"] for row in series),
                                 "zero_sss_steps": sum(row["sss_ppt"] == 0 for row in series),
                                 "integrated_net_salt_flux_kg_m2": sum(row["salt_ocean_kg_m2_s"] *
                                     row["dt_s"] for row in series)})
                    if key == "T14":
                        rows[-1].update(
                            min_sst_C=min(row["sst_C"] for row in series),
                            max_sst_C=max(row["sst_C"] for row in series),
                            simultaneous_zero_steps=sum(row["sss_ppt"] == 0 and row["sst_C"] == 0
                                                        for row in series),
                            max_sst_freezing_difference_C=max(
                                abs(row["sst_C"] - row["freezing_temperature_C"]) for row in series))
        write_csv(folder / "summary.csv", rows)
        evidence.append(folder)
    return metrics, evidence, note


def compact(metrics: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep the worst absolute sample of each identically thresholded metric."""
    worst: dict[tuple[str, float], dict[str, object]] = {}
    for metric in metrics:
        identity = (str(metric["name"]), float(metric["limit"]))
        if identity not in worst or abs(float(metric["actual"])) > abs(float(worst[identity]["actual"])):
            worst[identity] = metric
    return list(worst.values())


def line_plot(path: Path, series: list[tuple[str, list[tuple[float, float]]]],
              xlabel: str, ylabel: str) -> None:
    points = [point for _, values in series for point in values]
    if not points or any(not math.isfinite(value) for point in points for value in point):
        raise ValueError(f"missing/nonfinite graph data: {path}")
    xmin, xmax = min(x for x, _ in points), max(x for x, _ in points)
    ymin, ymax = min(y for _, y in points), max(y for _, y in points)
    xmax = max(xmax, xmin + 1e-12)
    ymax = max(ymax, ymin + 1e-12)
    elements = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="500" viewBox="0 0 900 500">',
                '<rect width="900" height="500" fill="white"/>',
                '<path d="M90 30 V420 H650" fill="none" stroke="black"/>']
    colors = ("#2065ab", "#b2182b", "#238b45", "#756bb1", "#e6550d", "#636363")
    for index, (label, values) in enumerate(series):
        color = colors[index % len(colors)]
        coordinates = " ".join(f"{90 + (x-xmin)/(xmax-xmin)*560:.3f},{420-(y-ymin)/(ymax-ymin)*390:.3f}"
                               for x, y in values)
        elements.append(f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="1.5"/>')
        elements.append(f'<text x="665" y="{35 + 17*index}" font-size="12" fill="{color}">{html.escape(label)}</text>')
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        x, y = 90 + fraction * 560, 420 - fraction * 390
        elements += [f'<text x="{x}" y="440" font-size="12">{xmin+fraction*(xmax-xmin):.3g}</text>',
                     f'<text x="8" y="{y}" font-size="12">{ymin+fraction*(ymax-ymin):.3g}</text>']
    elements += [f'<text x="280" y="475">{html.escape(xlabel)}</text>',
                 f'<text x="95" y="20">{html.escape(ylabel)}</text>', "</svg>"]
    path.write_text("\n".join(elements) + "\n")


def annual_plot_series(evidence: list[Path]) -> list[tuple[str, list[tuple[float, float]]]]:
    series: dict[str, list[tuple[float, float]]] = {"off": [], "on": []}
    for run in evidence:
        match = re.fullmatch(r"T10-annual-(True|False)-([1-4])", run.name)
        if not match:
            continue
        mode = "on" if match.group(1) == "True" else "off"
        segment = int(match.group(2)) - 1
        offset = sum(days for _, days in SEASONS[:segment])
        rows = csv_rows(run / "mushy_columns.csv")
        series[mode].extend((offset + row["step"] * row["dt_s"] / 86400,
                             row["ice_volume_m"]) for row in rows if row["cell"] == 2)
    if any(not points for points in series.values()):
        raise ValueError("annual plot is missing flag-off or flag-on evidence")
    return [(f"flag {mode}", sorted(points)) for mode, points in series.items()]


def graph_case(output: Path, key: str, evidence: list[Path]) -> str | None:
    if key == "T10-BGC":
        log = (evidence[0] / "harness.log").read_text()
        markers = ("BGC_ZERO_OK", "BGC_TINY_OK", "BGC_GUARD_OK")
        elements = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="300" viewBox="0 0 900 300">',
                    '<rect width="900" height="300" fill="white"/>',
                    '<text x="30" y="35" font-size="20">BGC initializer and guard evidence</text>']
        for index, marker in enumerate(markers):
            found = marker in log
            color = "#238b45" if found else "#b2182b"
            label = "marker present" if found else "marker missing"
            y = 90 + index * 65
            elements += [f'<circle cx="45" cy="{y}" r="8" fill="{color}"/>',
                         f'<text x="70" y="{y + 6}" font-size="18">{marker}: {label}</text>']
        elements.append("</svg>")
        name = f"{key}.svg"
        (output / name).write_text("\n".join(elements) + "\n")
        return name
    series = []
    xlabel, ylabel = "step", "Ice volume per cell area (m)"
    for run in evidence:
        if (run / "binary.csv").exists():
            rows = csv_rows(run / "binary.csv")
            for salt in sorted({row["salt_mass_fraction"] for row in rows}):
                series.append((f"salt mass fraction {salt:g}", [(row["specific_enthalpy_J_kg"],
                    row["liquid_mass_fraction"]) for row in rows if row["salt_mass_fraction"] == salt]))
            xlabel, ylabel = "Specific enthalpy (J/kg)", "Liquid mass fraction"
        elif (run / "mushy_columns.csv").exists():
            # Cell 2 is the slab target. Column inventories retain zero-ice
            # samples, whereas active-layer records legitimately disappear.
            rows = csv_rows(run / "mushy_columns.csv")
            series.append((run.name, [(row["step"], row["ice_volume_m"])
                                      for row in rows if row["cell"] == 2]))
            ylabel = "Slab ice volume per cell area (m)"
        elif (run / "phase.csv").exists() and key == "T1":
            series.append(("freshwater phase", [(row["q"], row["phi"]) for row in csv_rows(run / "phase.csv")]))
            xlabel, ylabel = "Enthalpy (J/m3)", "Liquid fraction"
        elif (run / "connectivity.csv").exists():
            series.append(("permeability", [(row["phi"], row["permeability"])
                                           for row in csv_rows(run / "connectivity.csv")]))
            xlabel, ylabel = "Liquid fraction", "Permeability (m2)"
        elif (run / "adapted.csv").exists():
            rows = csv_rows(run / "adapted.csv")
            for field in ("observed_temperature", "temperature"):
                series.append((field, [(row["depth_m"], row[field]) for row in rows]))
            xlabel, ylabel = "Depth (m)", "Temperature (C)"
    if key == "T10-brine":
        rows = [row for run in evidence if (run / "brine.csv").is_file()
                for row in csv_rows(run / "brine.csv")]
        series = [(label, sorted((row["q"], row["phi_after"]) for row in rows
                    if (row["profile"] <= 7) == controlled))
                  for label, controlled in (("controlled", True), ("integrated replay", False))]
        xlabel, ylabel = "Remapped enthalpy (J/m3)", "Liquid fraction"
    if key == "T2":
        series = [(run.name, [(row["sss"], row["temperature"]) for row in csv_rows(run / "frazil.csv")])
                  for run in evidence if (run / "frazil.csv").is_file()]
        xlabel, ylabel = "Ocean salinity (ppt)", "New frazil temperature (C)"
    if key in ("T5", "T12"):
        table = next(run / "summary.csv" for run in evidence if (run / "summary.csv").is_file())
        with table.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        series = []
        fields = ("temperature_C", "phi", "sice_ppt", "basal growth") if key == "T5" else ("temperature", "salt flux")
        for field in fields:
            series.append((field, [(float(upper), max(abs(float(row["actual"])) / float(row["limit"])
                for row in rows if row["fine_layers"] == str(upper) and field in row["name"]))
                for upper in ((7, 10, 14, 20, 28) if key == "T5" else (10, 14, 20, 28))]))
        xlabel, ylabel = "Finer ice-layer count", "Worst error / predeclared limit"
    if key == "T10":
        series = annual_plot_series(evidence)
        xlabel, ylabel = "Elapsed model time (days)", "Slab ice volume per cell area (m)"
    if key == "T10-cost":
        table = next(run / "summary.csv" for run in evidence if (run / "summary.csv").is_file())
        with table.open(newline="") as stream:
            costs = list(csv.DictReader(stream))
        series = [(f"{mode}: {label}", [(float(row["layers"]), float(row[field]))
                   for row in costs if row["mode"] == mode])
                  for mode in ("off", "on")
                  for field, label in (("model_wall_s", "wall"), ("model_cpu_s", "CPU"))]
        xlabel, ylabel = "Ice-layer count", "365-day model time (s)"
    if not series:
        return None
    name = f"{key}.svg"
    line_plot(output / name, series, xlabel, ylabel)
    return name


def validate_results(output: Path) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("schema") == 1:
        raise ValueError("legacy result schema uses old test numbering; use the original runner to validate or report it")
    if manifest.get("schema") not in (2, 3, RESULT_SCHEMA) or manifest.get("status") != "completed":
        raise ValueError("results are incomplete or have an unsupported schema")
    if "results.json" not in manifest["files"]:
        raise ValueError("results.json is not included in the evidence manifest")
    for name, digest in manifest["files"].items():
        path = (output / name).resolve()
        if not path.is_relative_to(output.resolve()) or sha256(path) != digest:
            raise ValueError(f"modified/missing evidence: {name}")
    records = json.loads((output / "results.json").read_text())
    if [row["case"] for row in records] != manifest["selection"]:
        raise ValueError("result selection does not match the manifest")
    if selected(",".join(manifest["selection"]), "core") != manifest["selection"]:
        raise ValueError("invalid result selection")
    counts = {"T1": 1, "T2": 4, "T3": 4, "T4": 2, "T5": 19, "T6": 2, "T7": 1,
              "T8": 6, "T9": 1, "T10-brine": 6, "T10-BGC": 1, "T10": 31,
              "T10-cost": 17, "T11": 6, "T12": 11, "T13": 11 if manifest["schema"] >= 3 else 10,
              "T14": 11}
    for record in records:
        if record["case"] == "T14" and manifest["schema"] < 4:
            raise ValueError("earlier T14 results include convergence grading; use their original runner, "
                             "not the boundary-only T14 definition")
        if record["kind"] != CASES[record["case"]].kind or not record["metrics"]:
            raise ValueError("missing metrics or incorrect scientific classification")
        if (len(record["evidence"]) != counts[record["case"]] or
                len(set(record["evidence"])) != counts[record["case"]]):
            raise ValueError(f"incomplete evidence selection: {record['case']}")
        if record["figure"] and record["figure"] not in manifest["files"]:
            raise ValueError(f"unlisted figure: {record['figure']}")
        for metric in record["metrics"]:
            recomputed = check(metric["name"], metric["actual"], metric["limit"])
            if metric != recomputed:
                raise ValueError(f"inconsistent scientific verdict: {record['case']}")
        for name in record["evidence"]:
            run = (output / name).resolve()
            if not run.is_relative_to(output.resolve()) or not run.is_dir():
                raise ValueError(f"missing/invalid evidence directory: {name}")
            if name == "T1-binary":
                required = ("binary.csv", "exact.csv")
            elif name in ("T13-layers", "T14-layers") and manifest["schema"] >= 3:
                required = ("summary.csv",) + tuple(
                    f"{record['case']}-{forcing}-{layers}-{view}.svg"
                    for layers in (7, 20) for forcing in ("control", "zero-sss")
                    for view in ("slab", "itd-mean", "itd-cat1", "itd-cat2",
                                 "itd-cat3", "itd-cat4", "itd-cat5"))
            elif name.endswith("-summary"):
                required = ("summary.csv",)
            elif name.endswith("-forcing"):
                required = ("control.txt", "zero-sss.txt", "provenance.json")
                if record["case"] == "T12":
                    required += ("summary.csv",)
            elif name.startswith("T2-phase-cap-") or name == "T10-brine-constants":
                required = ("harness.log", "constants.csv", "phase.csv", "frazil.csv")
            elif name == "T7-connectivity":
                required = ("harness.log", "connectivity.csv")
            elif name == "T9-observation":
                required = ("harness.log", "sections.in", "adapted.csv", "source-integrals.csv")
            elif name == "T10-brine-replay":
                required = ("harness.log", "profiles.in", "brine.csv")
            elif name == "T10-BGC-initializer":
                required = ("harness.log", "harness.F90")
            else:
                required = ("run.log", "icepack_in", "timing.json")
                if name not in ("T2-reference", "T3-without-diagnostics"):
                    required += ("mushy_columns.csv", "mushy_layers.csv")
                if record["case"] in ("T13", "T14") and manifest["schema"] >= 3:
                    required += ("mushy_snow.csv",)
                    if record["case"] == "T13" or any(f"-{layers}-" in name for layers in (7, 20)):
                        t13_profiles(run, 7 if "-7-" in name else 20)
                if not any(path.is_file() and str(path.relative_to(output.resolve())) in manifest["files"]
                           for path in (run / "restart").glob("iced.*")):
                    raise ValueError(f"missing/unlisted restart evidence: {name}")
            for filename in required:
                path = run / filename
                if not path.is_file() or str(path.relative_to(output.resolve())) not in manifest["files"]:
                    raise ValueError(f"missing/unlisted evidence: {name}/{filename}")
            if (run / "mushy_layers.csv").is_file():
                layer_checks(run)
                if record["case"] == "T14":
                    actual = compact(freezing_boundary_checks(run, "-zero-sss-" in name))
                    recorded = {row["name"]: row for row in record["metrics"]}
                    if any(row["limit"] != recorded[row["name"]]["limit"] or
                           abs(row["actual"]) > abs(recorded[row["name"]]["actual"]) + 1e-12
                           for row in actual):
                        raise ValueError("recomputed paired-boundary metrics disagree with the report")
            if record["case"] == "T8":
                actual = compact(budget_checks(run))
                recorded = {row["name"]: row for row in record["metrics"]}
                if any(row["limit"] != recorded[row["name"]]["limit"] or
                       abs(row["actual"]) > abs(recorded[row["name"]]["actual"]) + 1e-12 for row in actual):
                    raise ValueError("recomputed conservation metrics disagree with the report")
    return manifest


def report(output: Path) -> None:
    validate_results(output)
    records = json.loads((output / "results.json").read_text())
    lines = ["# Mushy-layer scientific tests", "",
             "Execution, scientific acceptance, and descriptive results are separate.",
             "T5/T12 retain their fixed convergence criteria; T14 checks the prescribed ocean boundary.",
             "Heat residuals cover local thermodynamics, not full coupled/pond energy or salt closure.", "",
             "| Test | Scope | Execution | Scientific result |",
             "| --- | --- | --- | --- |"]
    for row in records:
        passed = all(metric["passed"] for metric in row["metrics"])
        verdict = ("criteria met" if passed else "criteria NOT met") if row["kind"] != "descriptive" else "descriptive"
        lines.append(f"| {row['case']} | {CASES[row['case']].title} | completed | {verdict} |")
    for row in records:
        lines += ["", f"## {row['case']}: {CASES[row['case']].title}", "", row["note"], "",
                  "| Metric | Worst signed sample | Absolute limit | Met |", "| --- | ---: | ---: | --- |"]
        for metric in row["metrics"]:
            lines.append(f"| {metric['name']} | {metric['actual']:.10g} | {metric['limit']:.10g} | {metric['passed']} |")
        for name in row["evidence"]:
            table = output / name / "summary.csv"
            if not table.is_file() and row["case"] == "T9":
                table = output / name / "adapted.csv"
            if table.is_file():
                with table.open(newline="") as stream:
                    values = list(csv.reader(stream))
                lines += ["", "| " + " | ".join(values[0]) + " |",
                          "| " + " | ".join("---" for _ in values[0]) + " |"]
                lines += ["| " + " | ".join(record) + " |" for record in values[1:]]
        if row["figure"]:
            lines += ["", f"![{row['case']} measured results]({row['figure']})"]
        if row["case"] in ("T13", "T14") and f"{row['case']}-layers" in row["evidence"]:
            lines += ["", "### Layer-by-layer flag comparisons", "",
                      "Daily profiles show both flags on common axes; inactive ice and absent snow are gaps.",
                      "The full-ITD mean is weighted by category ice/snow volume. "
                      "Individual category and slab plots are provided separately.", ""]
            for layers in (7, 20):
                for forcing in ("control", "zero-sss"):
                    prefix = f"{row['case']}-layers/{row['case']}-{forcing}-{layers}"
                    lines.append(f"- {forcing}, {layers} ice layers: " +
                                 ", ".join(f"[{view}]({prefix}-{view}.svg)" for view in
                                           ("slab", "itd-mean", "itd-cat1", "itd-cat2",
                                            "itd-cat3", "itd-cat4", "itd-cat5")))
        lines += ["", "Raw evidence: " + ", ".join(f"[{name}/]({name}/)" for name in row["evidence"])]
    lines += ["", "## Input attribution", ""]
    manifest = json.loads((output / "manifest.json").read_text())
    for name in manifest["datasets"]:
        spec = DATASETS[name]
        lines += [f"- {spec['attribution']} {spec['source']} SHA-256: `{spec['sha256']}`."]
    (output / "TEST_RESULTS.md").write_text("\n".join(lines) + "\n")


def run_tests(args: argparse.Namespace) -> int:
    args.output = args.output.resolve()
    args.icepack_root = args.icepack_root.resolve()
    keys = selected(args.tests, args.suite)
    for path in (args.output.resolve(), args.icepack_root.resolve()):
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(path)):
            raise ValueError("native case/build paths must not contain whitespace or shell metacharacters")
    args.output.mkdir(parents=True, exist_ok=False)
    needed = sorted({name for key in keys for name in CASES[key].datasets})
    data = {name: acquire(name, args.cache_dir, args.offline) for name in needed}
    runner = Runner(args, data)
    tool_digest = sha256(Path(__file__))
    source_files = sorted((runner.root / "columnphysics").glob("*.F90"))
    source_files += sorted((runner.root / "configuration" / "driver").glob("*.F90"))
    before = {str(path.relative_to(runner.root)): sha256(path) for path in source_files}
    records = []
    write_json(args.output / "execution.json", {"status": "running", "selection": keys})
    for key in keys:
        print(f"{key}: running", flush=True)
        if CASES[key].core:
            metrics, evidence, note = core_case(runner, key)
        else:
            metrics, evidence, note = extended_case(runner, key)
        if not metrics or not evidence:
            raise ValueError(f"empty selected-test evidence: {key}")
        figure = graph_case(args.output, key, evidence)
        records.append({"case": key, "kind": CASES[key].kind, "metrics": compact(metrics),
                        "evidence": [str(path.relative_to(args.output.resolve())) for path in evidence],
                        "note": note, "figure": figure})
        write_json(args.output / "results.json", records)
        print(f"{key}: " + ("criteria met" if all(metric["passed"] for metric in metrics)
                           else "criteria NOT met"), flush=True)
    after = {str(path.relative_to(runner.root)): sha256(path) for path in source_files}
    if before != after:
        raise RuntimeError("model source changed during validation")
    if sha256(Path(__file__)) != tool_digest:
        raise RuntimeError("test tool changed during validation; rerun with the final implementation")
    write_json(args.output / "execution.json", {"status": "completed", "selection": keys})
    files = {"results.json": sha256(args.output / "results.json")}
    for record in records:
        if record["figure"]:
            files[record["figure"]] = sha256(args.output / record["figure"])
        for name in record["evidence"]:
            for path in (args.output / name).rglob("*"):
                if path.is_file() and path.name not in ("icepack", "harness"):
                    files[str(path.relative_to(args.output))] = sha256(path)
    for case, _, _ in runner.builds.values():
        for pattern in ("icepack.settings", "Macros.*", "env.*", "build.log", "build-timing.json"):
            for path in case.glob(pattern):
                files[str(path.relative_to(args.output))] = sha256(path)
    write_json(args.output / "manifest.json", {
        "schema": RESULT_SCHEMA, "status": "completed", "selection": keys, "files": files,
        "source_sha256": before, "datasets": needed, "baseline_ref": args.baseline_ref,
        "baseline_commit": ((args.output / "reference-source" / ".git" / "HEAD").read_text().strip()
                            if "T2" in keys else None),
        "tool_sha256": tool_digest, "machine": args.machine,
        "environment": args.environment,
    })
    report(args.output)
    return int(any(not metric["passed"] for row in records if row["kind"] != "descriptive"
                   for metric in row["metrics"]))


def self_test() -> int:
    """Negative-path tests live here to keep the tool a single Python file."""
    import io
    import unittest
    from unittest.mock import patch

    class ToolTests(unittest.TestCase):
        def test_t14_does_not_run_or_grade_layer_refinement(self):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                runner = unittest.mock.Mock(output=root)
                runner.model.side_effect = lambda name, *args, **kwargs: root / name
                with patch(__name__ + ".forcing_pair", return_value=(
                        root / "atmosphere.txt", root / "T14-forcing/control.txt",
                        root / "T14-forcing/zero-sss.txt")), \
                        patch(__name__ + ".layer_checks", return_value=[]), \
                        patch(__name__ + ".freezing_boundary_checks",
                              return_value=[check("boundary tracking", 0.0)]), \
                        patch(__name__ + ".t13_layer_comparisons"), \
                        patch(__name__ + ".write_csv"), \
                        patch(__name__ + ".compare_t12") as refinement:
                    metrics, evidence, _ = extended_case(runner, "T14")
                refinement.assert_not_called()
                self.assertEqual(len(runner.model.call_args_list), 8)
                self.assertEqual({call.args[2] for call in runner.model.call_args_list}, {7, 20})
                self.assertEqual(len(evidence), 11)
                self.assertTrue(all(metric["passed"] for metric in metrics))
                self.assertFalse(any("refinement" in metric["name"] for metric in metrics))

        def test_paired_forcing_checks_actual_sst_and_exact_zero_events(self):
            samples = [{"step": step, "cell": cell, "sss_ppt": 0.0,
                        "sst_C": 0.0, "freezing_temperature_C": 0.0}
                       for step in range(1, 13) for cell in (2, 3)]
            with patch(__name__ + ".csv_rows", return_value=samples):
                self.assertTrue(all(metric["passed"] for metric in
                                    freezing_boundary_checks(Path("unused"), True, steps=12)))
                samples[0]["sst_C"] = -1.8
                self.assertTrue(any(not metric["passed"] for metric in
                                    freezing_boundary_checks(Path("unused"), True, steps=12)))
                samples[0]["sst_C"] = 0.0
                samples[0]["sss_ppt"] = 1e-12
                self.assertTrue(any(not metric["passed"] for metric in
                                    freezing_boundary_checks(Path("unused"), True, steps=12)))
                samples.pop()
                with self.assertRaisesRegex(ValueError, "incomplete paired-forcing"):
                    freezing_boundary_checks(Path("unused"), True, steps=12)

        def test_long_forcing_paths_use_native_local_layout(self):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                sources = tuple(root / (name * 90 + ".txt") for name in ("a", "o"))
                for source in sources:
                    source.write_bytes(source.name.encode())
                run = root / "run"
                values = stage_forcing(run, sources)
                for key, source in zip(("atm_data_file", "ocn_data_file"), sources):
                    filename = values[key].strip("'")
                    path = run / values["data_dir"].strip("'") / "NICE_2015" / filename
                    self.assertLess(len(filename), 80)
                    self.assertEqual(path.read_bytes(), source.read_bytes())

        def test_offline_missing_and_corrupt_cache(self):
            with tempfile.TemporaryDirectory() as folder:
                cache = Path(folder)
                with self.assertRaises(FileNotFoundError):
                    acquire("profiles", cache, True)
                (cache / DATASETS["profiles"]["filename"]).write_bytes(b"corrupt")
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    acquire("profiles", cache, True)

        def test_verified_download_and_checksum_failure(self):
            payload = b"small public test input\n"
            spec = {"url": "https://example.invalid/data", "filename": "test.tsv",
                    "sha256": hashlib.sha256(payload).hexdigest()}
            with tempfile.TemporaryDirectory() as folder, patch.dict(DATASETS, {"unit": spec}):
                cache = Path(folder)
                with patch("shutil.which", return_value=None), patch(
                        "urllib.request.urlopen", return_value=io.BytesIO(b"wrong")):
                    with self.assertRaisesRegex(ValueError, "download checksum mismatch"):
                        acquire("unit", cache, False)
                self.assertFalse((cache / "test.tsv").exists())
                with patch("shutil.which", return_value=None), patch(
                        "urllib.request.urlopen", return_value=io.BytesIO(payload)):
                    acquired = acquire("unit", cache, False)
                self.assertEqual(acquired.read_bytes(), payload)
                self.assertEqual(acquire("unit", cache, True), acquired)

        def test_archive_rejects_traversal_and_links(self):
            for filename, kind in (("../outside", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                                   ("link", tarfile.SYMTYPE), ("device", tarfile.CHRTYPE)):
                with self.subTest(filename=filename), tempfile.TemporaryDirectory() as folder:
                    archive = Path(folder) / "input.tar.gz"
                    info = tarfile.TarInfo(filename)
                    info.type = kind
                    with tarfile.open(archive, "w:gz") as stream:
                        stream.addfile(info)
                    destination = Path(folder) / "out"
                    destination.mkdir()
                    with self.assertRaisesRegex(ValueError, "unsafe archive"):
                        extract_data(archive, destination)
                    self.assertEqual(list(destination.iterdir()), [])

        def test_freshwater_mixed_phase_is_not_forced_solid(self):
            constants = {"rhoi": 917.0, "latent": 334000.0, "cp_ice": 2106.0,
                         "rhow": 1026.0, "cp_ocn": 4218.0}
            latent = constants["rhoi"] * constants["latent"]
            for fraction in (0.0, 1e-12, 0.25, 0.75, 1.0):
                t, phi = freshwater_state((fraction - 1) * latent, constants)
                self.assertEqual(t, 0)
                self.assertAlmostEqual(phi, fraction, places=14)

        def test_positive_salinity_parity_and_frazil_enthalpy_are_checked(self):
            constants = {"rhoi": 917.0, "latent": 334000.0, "cp_ice": 2106.0,
                         "rhow": 1026.0, "cp_ocn": 4218.0, "delta": 1.0,
                         "cap": 0.0, "phi_init": 0.85, "default_flag": 0.0}
            state = {"q": -constants["rhoi"] * constants["latent"], "salinity": 0.0,
                     "temperature": 0.0, "phi": 0.0, "off_temperature": 0.0, "off_phi": 0.0}
            states = [dict(state) for _ in range(10)] + [dict(state, salinity=4.0) for _ in range(8)]
            states[-1]["off_phi"] = 0.1
            frazil = [{"sss": float(s), "salt": s * s / 4 if s <= 2 else s - 1,
                       "temperature": 0.0, "q": -constants["rhoi"] * constants["latent"],
                       "liquidus": 1.0, "phi": 0.0} for s in range(53)]
            with patch(__name__ + ".csv_rows", side_effect=[[constants], states, frazil]):
                metrics, _ = phase_checks(Path("unused"))
            self.assertTrue(any(metric["name"] == "positive-salinity flag parity phi" and
                                not metric["passed"] for metric in metrics))
            enthalpy_metrics = [metric for metric in metrics
                                if metric["name"] == "native frazil enthalpy reconstruction (J/m3)"]
            self.assertTrue(len(enthalpy_metrics) == 53 and
                            all(metric["passed"] for metric in enthalpy_metrics))
            frazil[0]["q"] += 1e-3
            with patch(__name__ + ".csv_rows", side_effect=[[constants], states, frazil]):
                corrupted_metrics, _ = phase_checks(Path("unused"))
            self.assertTrue(any(metric["name"] == "native frazil enthalpy reconstruction (J/m3)" and
                                not metric["passed"] for metric in corrupted_metrics))

        def test_zero_sss_frazil_checks_require_active_salt_free_production(self):
            inactive = [{"frazil_water_kg_m2": 0.0, "sss_ppt": 0.0,
                         "new_frazil_salinity_ppt": 0.0,
                         "frazil_correction_salt_source_kg_m2": 0.0}]
            self.assertFalse(zero_sss_frazil_checks(inactive)[0]["passed"])
            active = [{"frazil_water_kg_m2": 0.1, "sss_ppt": 0.0,
                       "new_frazil_salinity_ppt": 0.0,
                       "frazil_correction_salt_source_kg_m2": 0.0}]
            self.assertTrue(all(metric["passed"] for metric in zero_sss_frazil_checks(active)))
            active[0]["new_frazil_salinity_ppt"] = 1e-6
            self.assertFalse(zero_sss_frazil_checks(active)[2]["passed"])
            active[0]["new_frazil_salinity_ppt"] = 0.0
            active[0]["frazil_correction_salt_source_kg_m2"] = 1e-6
            self.assertFalse(zero_sss_frazil_checks(active)[3]["passed"])

        def test_validation_rejects_unlisted_missing_evidence(self):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                run = root / "T1-binary"
                run.mkdir()
                for name in ("binary.csv", "exact.csv"):
                    (run / name).write_text("value\n0\n")
                records = [{"case": "T1", "kind": "acceptance", "metrics": [check("example", 0)],
                            "evidence": ["T1-binary"], "figure": None}]
                write_json(root / "results.json", records)
                files = {str(path.relative_to(root)): sha256(path)
                         for path in (root / "results.json", run / "binary.csv", run / "exact.csv")}
                manifest = {"schema": RESULT_SCHEMA, "status": "completed", "selection": ["T1"], "files": files}
                write_json(root / "manifest.json", manifest)
                validate_results(root)
                del files["T1-binary/binary.csv"]
                (run / "binary.csv").unlink()
                write_json(root / "manifest.json", manifest)
                with self.assertRaisesRegex(ValueError, "missing/unlisted evidence"):
                    validate_results(root)

        def test_graphs_include_zero_ice_and_measured_cost(self):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                run, totals = root / "run", root / "totals"
                run.mkdir()
                totals.mkdir()
                write_csv(run / "mushy_columns.csv", [
                    {"step": step, "cell": 2, "ice_volume_m": 0.0} for step in (1, 2)])
                self.assertEqual(graph_case(root, "T3", [run]), "T3.svg")
                write_csv(totals / "summary.csv", [
                    {"layers": layers, "mode": mode, "model_wall_s": layers * 1.5,
                     "model_cpu_s": layers * 1.2}
                    for layers in (7, 20) for mode in ("off", "on")])
                self.assertEqual(graph_case(root, "T10-cost", [run, totals]), "T10-cost.svg")
                self.assertIn("365-day model time", (root / "T10-cost.svg").read_text())
                (run / "harness.log").write_text("BGC_ZERO_OK\nBGC_TINY_OK\n")
                self.assertEqual(graph_case(root, "T10-BGC", [run]), "T10-BGC.svg")
                graph = (root / "T10-BGC.svg").read_text()
                self.assertIn("BGC_TINY_OK: marker present", graph)
                self.assertIn("BGC_GUARD_OK: marker missing", graph)

        def test_annual_plot_uses_season_offsets(self):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                runs = []
                for enabled in ("False", "True"):
                    for segment in (2, 1):
                        run = root / f"T10-annual-{enabled}-{segment}"
                        run.mkdir()
                        write_csv(run / "mushy_columns.csv", [
                            {"step": 24, "dt_s": 3600, "cell": 2,
                             "ice_volume_m": segment}])
                        runs.append(run)
                series = dict(annual_plot_series(runs))
                self.assertEqual(series["flag off"], [(1.0, 1.0), (91.0, 2.0)])
                self.assertEqual(series["flag on"], [(1.0, 1.0), (91.0, 2.0)])
                self.assertEqual(graph_case(root, "T10", runs), "T10.svg")
                self.assertIn("Elapsed model time (days)", (root / "T10.svg").read_text())

        def test_t13_profiles_weight_categories_and_preserve_gaps(self):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                ice, snow = [], []
                for day, category, volume in ((1, 1, 1), (1, 2, 3), (3, 1, 1)):
                    for cell in (2, 3):
                        for layer in (1, 2):
                            ice.append({"step": 48 * day, "cell": cell, "category": category,
                                        "layer": layer, "aicen_fraction": 0.5, "vicen_m": volume,
                                        "vsnon_m": volume if day == 1 else 0,
                                        "temperature_C": 10 * category + layer,
                                        "sice_ppt": category, "phi": 0.1 * category})
                        for layer in range(1, 6):
                            snow.append({"step": 48 * day, "cell": cell, "category": category,
                                         "layer": layer, "temperature_C": 10 * category + layer})
                write_csv(root / "mushy_layers.csv", ice)
                write_csv(root / "mushy_snow.csv", snow)
                profiles = t13_profiles(root, 2, days=3)
                series = t13_series(profiles, 3, None, 2, days=3)
                self.assertEqual(series[1][0], [18.5, None, 11.0])
                self.assertEqual(series[0][0], [18.5, None, None])
                self.assertEqual(series[4][1], [6.0, None, None])
                t13_plot(root / "comparison.svg", series, series, "example", days=3)
                self.assertIn("<circle ", (root / "comparison.svg").read_text())
                write_csv(root / "mushy_layers.csv", ice[:-1])
                with self.assertRaisesRegex(ValueError, "incomplete T13 layer"):
                    t13_profiles(root, 2, days=3)

        def test_renumbering_and_legacy_results(self):
            self.assertEqual(selected("T11,T12,T13", "core"), ["T11", "T12", "T13"])
            self.assertEqual(CASES["T11"].kind, "descriptive")
            self.assertEqual(CASES["T12"].kind, "convergence")
            self.assertEqual(CASES["T13"].kind, "descriptive")
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                write_json(root / "manifest.json", {"schema": 1, "status": "completed"})
                with self.assertRaisesRegex(ValueError, "old test numbering.*original runner"):
                    validate_results(root)
                write_json(root / "results.json", [{"case": "T14", "kind": "convergence"}])
                write_json(root / "manifest.json", {
                    "schema": 3, "status": "completed", "selection": ["T14"],
                    "files": {"results.json": sha256(root / "results.json")}})
                with self.assertRaisesRegex(ValueError, "earlier T14.*original runner"):
                    validate_results(root)

        def test_inputs_and_metrics_fail_explicitly(self):
            with self.assertRaises(ValueError):
                selected("T1,T99", "core")
            with self.assertRaises(ValueError):
                namelist("npt=1\nnpt=2\n", {"npt": "3"})
            with self.assertRaises(ValueError):
                check("nonfinite", math.nan)
            self.assertFalse(check("unmet criterion", 2, 1)["passed"])

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ToolTests))
    return int(not result.wasSuccessful())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="show all performed core and extended entries")
    sub.add_parser("self-test", help="check input safety, acquisition and scientific reporting logic")
    for action in ("run", "fetch-data"):
        command_parser = sub.add_parser(action)
        command_parser.add_argument("--suite", choices=("core", "extended"), default="core")
        command_parser.add_argument("--tests", help="comma-separated case IDs; overrides --suite")
        command_parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache/icepack/mushy")
        command_parser.add_argument("--offline", action="store_true")
        if action == "run":
            command_parser.add_argument("--icepack-root", type=Path, default=Path(__file__).resolve().parents[3])
            command_parser.add_argument("--output", type=Path, required=True, help="new, private result directory")
            command_parser.add_argument("--machine", required=True, help="native icepack.setup --mach")
            command_parser.add_argument("--environment", required=True, help="native icepack.setup --env")
            command_parser.add_argument("--baseline-ref", default="HEAD", help="git ref for default-off T2 comparison")
    for action in ("report", "validate"):
        command_parser = sub.add_parser(action)
        command_parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "list":
            for key, case in CASES.items():
                print(f"{key:12} {'core' if case.core else 'extended':8} {case.kind:12} {case.title}")
        elif args.action == "self-test":
            return self_test()
        elif args.action == "fetch-data":
            keys = selected(args.tests, args.suite)
            for name in sorted({name for key in keys for name in CASES[key].datasets}):
                print(f"{name}: {acquire(name, args.cache_dir, args.offline)}")
        elif args.action == "run":
            return run_tests(args)
        elif args.action == "report":
            report(args.results)
            print(args.results / "TEST_RESULTS.md")
        else:
            validate_results(args.results)
            print("Evidence hashes, completeness, and recorded scientific verdicts verified.")
        return 0
    except (OSError, ValueError, RuntimeError, tarfile.TarError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
