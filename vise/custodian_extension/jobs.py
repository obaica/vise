# -*- coding: utf-8 -*-

import json
import os
import shutil
from collections import UserList
from glob import glob
from pathlib import Path
from typing import Optional, List
from uuid import uuid4
import numpy as np

from custodian.vasp.jobs import VaspJob
from monty.json import MSONable
from monty.json import MontyEncoder
from monty.serialization import loadfn
from pymatgen.core.structure import Structure
from pymatgen.io.vasp import Poscar, Kpoints, Vasprun
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from vise.config import SYMMETRY_TOLERANCE, ANGLE_TOL, KPT_INIT_DENSITY, \
    KPT_FACTOR
from vise.input_set.input_set import ViseInputSet
from vise.input_set.task import Task
from vise.input_set.xc import Xc
from vise.util.error_classes import VaspNotConvergedError, KptNotConvergedError
from vise.util.logger import get_logger

""" Provides structure optimization and kpt convergence jobs for VASP runs. """

logger = get_logger(__name__)


__maintainer__ = "Yu Kumagai"


VASP_INPUT_FILES = {"INCAR", "POSCAR", "POTCAR", "KPOINTS"}
VASP_SAVED_FILES = {"INCAR", "vasprun.xml", "CONTCAR", "OUTCAR"}
VASP_FINISHED_FILES = {i + ".finish" for i in VASP_SAVED_FILES}


def rm_wavecar(remove_current: bool,
               remove_subdirectories: Optional[bool] = False) -> None:
    """Remove WAVECARs at the current directory and subdirectories."""
    if remove_current:
        try:
            os.remove("WAVECAR")
        except FileNotFoundError:
            logger.log("WAVECAR does not exist at the current directory.")
            pass

    if remove_subdirectories:
        for wavecar in Path(".").glob('**/WAVECAR'):
            wavecar.unlink()


class StructureOptResult(MSONable):
    def __init__(self,
                 uuid: int,
                 energy_per_atom: float,
                 num_kpt: list,
                 final_structure: Structure,
                 final_sg: int,
                 kpt_density: float = None,
                 initial_structure: Optional[Structure] = None,
                 initial_sg: Optional[int] = None,
                 prev_structure_opt_uuid: Optional[int] = None):
        """Container object with structure optimization results.

        Args:
            uuid (int):
                Int of UUID of the present calculation.
            energy_per_atom (float):
                Energy per atom in eV.
            num_kpt (list):
                Numbers of k-points along three directions. E.g. [4, 6, 8]
            final_structure (Structure):
                Final structure.
            final_sg (int):
                Final space group.
            kpt_density (float):
                K-point density used for determining num_kpt.
            initial_structure (Structure):
                Initial structure.
            initial_sg (int):
                Initial space group.
            prev_structure_opt_uuid (int):
                Previous StructureOptResult object.
        """

        self.uuid = uuid
        self.energy_per_atom = energy_per_atom
        self.num_kpt = list(num_kpt)
        self.kpt_density = kpt_density
        self.final_structure = final_structure
        self.final_sg = final_sg
        self.initial_structure = initial_structure
        self.initial_sg = initial_sg
        self.prev_structure_opt_uuid = prev_structure_opt_uuid

    @classmethod
    def from_dir(cls,
                 dir_name: str,
                 kpoints: Optional[str] = "KPOINTS",
                 poscar: Optional[str] = "POSCAR.orig",
                 contcar: Optional[str] = "CONTCAR.finish",
                 vasprun: Optional[str] = "vasprun.xml.finish",
                 symprec: float = SYMMETRY_TOLERANCE,
                 angle_tolerance: float = ANGLE_TOL,
                 prev_structure_opt: Optional["StructureOptResult"] = None,
                 ) -> "StructureOptResult":
        """Constructor from directory and previous StructureOptResult if exists

        Note: Generate in initial_structure and initial_sg from previous
              StructureOptResult if exists.

        Args:
            dir_name (str):
                Dirname
            kpoints (str):
            poscar (str):
            contcar (str):
            vasprun (str):
                Vasp input and output file names
            symprec (float):
                Distance precision in Angstrom used for symmetry analysis.
            angle_tolerance (float):
                Angle precision in degrees used for symmetry analysis.
            prev_structure_opt (StructureOptResult):
                Previous StructureOptResult

        Return:
            StructureOptResult object
        """
        d_path = Path(dir_name)

        k = Kpoints.from_file(d_path / kpoints)
        num_kpt = k.kpts[0]

        try:
            vise = ViseInputSet.load_json(d_path / "vise.json")
            kpt_density = vise.kwargs["kpt_density"]
        except FileNotFoundError:
            print("vise.json does not exist")
            raise

        final_structure = Structure.from_file(d_path / contcar)
        sga = SpacegroupAnalyzer(structure=final_structure,
                                 symprec=symprec,
                                 angle_tolerance=angle_tolerance)
        symmetry_dataset = sga.get_symmetry_dataset()
        final_sg = symmetry_dataset["number"]

        v = Vasprun(d_path / vasprun)
        energy_per_atom = v.final_energy / len(final_structure)

        if prev_structure_opt:
            initial_structure = prev_structure_opt.final_structure
            initial_sg = prev_structure_opt.final_sg
            prev_structure_opt_uuid = prev_structure_opt.uuid
        else:
            initial_structure = Structure.from_file(d_path / poscar)
            sga = SpacegroupAnalyzer(structure=initial_structure,
                                     symprec=symprec,
                                     angle_tolerance=angle_tolerance)
            initial_symmetry_dataset = sga.get_symmetry_dataset()
            initial_sg = initial_symmetry_dataset["number"]
            prev_structure_opt_uuid = None

        return cls(uuid=int(uuid4()),
                   energy_per_atom=energy_per_atom,
                   num_kpt=num_kpt,
                   kpt_density=kpt_density,
                   final_structure=final_structure,
                   final_sg=final_sg,
                   initial_structure=initial_structure,
                   initial_sg=initial_sg,
                   prev_structure_opt_uuid=prev_structure_opt_uuid)

    @property
    def is_sg_changed(self):
        return self.initial_sg != self.final_sg

    @property
    def dirname(self):
        """Used for generating and parsing directory in k-point convergence"""

        name = [f"kpt{self.num_kpt[0]}x{self.num_kpt[1]}x{self.num_kpt[2]}",
                f"pre-sg{self.initial_sg}", f"pos-sg{self.final_sg}"]
        return '_'.join(name)

    def to_json_file(self, filename: str) -> None:
        with open(filename, 'w') as fw:
            json.dump(self.as_dict(), fw, indent=2, cls=MontyEncoder)

    @classmethod
    def load_json(cls, filename):
        return loadfn(filename)

    def __str__(self):
        outs = [f"k-points: {self.num_kpt}",
                f"k-point density {self.kpt_density}",
                f"space group: {self.initial_sg} -> {self.final_sg}",
                f"energy per atom (eV): {self.energy_per_atom}",
                f"uuid: {self.uuid}",
                f"prev_uuid: {self.prev_structure_opt_uuid}"]
        return "\n".join(outs)


class KptConvResult(UserList):

    def __init__(self,
                 structure_opt_results: List[StructureOptResult],
                 convergence_energy_criterion: float,
                 num_kpt_check: int = 2,
                 symprec: float = SYMMETRY_TOLERANCE):
        """Container object with k-point convergence results."

        structure_opt_results (list):
            List of StructureOptResult. The inner objects are sorted to be
            linked to be consistent with prev_structure_opt_uuid.
        convergence_criterion:
            Convergence energy criterion as a function of number of k points in
            eV / atom.
        num_kpt_check:
            Number of K-point set that is used for checking the convergence.
            For example, when it is 2, we calculate
            num_kpt = [4, 4, 4], [6, 6, 6], [8, 8, 8]
            and check whether result with [4, 4, 4] is converged with
            the results of [6, 6, 6] and [8, 8, 8].
        symprec (float):
            Distance precision in Angstrom used for symmetry analysis.
            This is also checked if the lattice constants are converged.
        """
        self.convergence_criterion = convergence_energy_criterion
        self.num_kpt_check = num_kpt_check
        self.symprec = symprec
        super().__init__(self.sort_results(structure_opt_results))

    @classmethod
    def from_dirs(cls,
                  convergence_criterion: float,
                  dirs: Optional[list] = None,
                  str_opt_filename: str = "structure_opt.json",
                  num_kpt_check: int = 2,
                  symprec: float = SYMMETRY_TOLERANCE) -> "KptConvResult":
        """Constructor from directories

        convergence_criterion:
        num_kpt_check:
        symprec:
            See docstrings of the constructor.
        dirs:
            Directory names to be parsed.
        structure_opt_filename (list):
            Json filename for StructureOptResult.
        symprec (float):

        Return:
            KptConvResult object.
        """
        dirs = dirs or glob("*kpt*/")
        str_opts = [StructureOptResult.load_json(Path(d, str_opt_filename))
                    for d in dirs]
        return cls(str_opts, convergence_criterion, num_kpt_check, symprec)

    @staticmethod
    def sort_results(results: list) -> list:
        """Sort StructureOptResult list using prev_structure_opt_uuid """
        sorted_results = [r for r in results if not r.prev_structure_opt_uuid]
        for i in range(len(results)):
            sorted_results += \
                [r for r in results
                 if r.prev_structure_opt_uuid == sorted_results[-1].uuid]
        return sorted_results

    @property
    def space_groups(self):
        return [self[0].initial_sg] + [i.final_sg for i in self]

    @property
    def conv_str_opt_result(self):
        """Return converged StructureOptResult.

        If not converged, return False.
        """
        if len(self) <= self.num_kpt_check:
            return False

        # The target index is the num_kpt_check-th last.
        target_idx = -(self.num_kpt_check + 1)

        # Number of space groups is incremented from calc results by 1
        # so need to check target_idx - 1.
        if len(set(self.space_groups[(target_idx - 1):])) > 1:
            return False

        for i in range(self.num_kpt_check):
            target_energy = self[target_idx].energy_per_atom
            compared_energy = self[target_idx + i].energy_per_atom
            energy_diff = target_energy - compared_energy
            if abs(energy_diff) > self.convergence_criterion:
                logger.log("Energy is not converged, yet")
                return False

            # Check convergence of lattice.
            target_lat = self[target_idx].final_structure.lattice.matrix
            compared_lat = self[target_idx + i].final_structure.lattice.matrix
            if not np.allclose(target_lat, compared_lat,  rtol=0,
                               atol=self.symprec):
                logger.log("Structure is not converged, yet")
                return False

        return self[target_idx]

    @property
    def is_sg_changed(self):
        return len(set(self.space_groups)) > 1

    def __str__(self):
        outs = [f"{'kpt':>10} {'energy/atom':>12}"]
        for result in self:
            outs.append(f"{'x'.join(map(str, result.num_kpt)):>10} "
                        f"{result.energy_per_atom}:>12:4")

        outs.append("")


    # TODO: show/hold warning in case the energy is increased when the symmetry
    #  is increased.


class ViseVaspJob(VaspJob):
    def __init__(self,
                 vasp_cmd: list,
                 gamma_vasp_cmd: Optional[list] = None,
                 output_file: str = "vasp.out",
                 stderr_file: str = "std_err.txt",
                 suffix: str = "",
                 final: bool = True,
                 backup: bool = True,
                 auto_continue: bool = False):
        """
        Override constructor to close some options as some options use Incar
        instead of VisaIncar.

        Args: See docstrings of VaspJob.
        """
        # Should be fine for vasp.5.4.4
        if gamma_vasp_cmd is None:
            gamma_vasp_cmd = vasp_cmd[:-1]
            gamma_vasp_cmd.append(vasp_cmd[-1].replace("std", "gam"))

        # Note that only list instance is accepted for vasp_cmd in VaspJob at
        # ver.2019.8.24.
        super().__init__(vasp_cmd=vasp_cmd,
                         output_file=output_file,
                         stderr_file=stderr_file,
                         suffix=suffix,
                         final=final,
                         backup=backup,
                         auto_npar=False,
                         auto_gamma=True,
                         settings_override=None,
                         gamma_vasp_cmd=gamma_vasp_cmd,
                         copy_magmom=False,
                         auto_continue=auto_continue)

    def postprocess(self):
        """
        """
        for f in VASP_SAVED_FILES | {self.output_file}:
            if os.path.exists(f):
                shutil.copy(f, "{}{}".format(f, self.suffix))

        # Remove continuation so if a subsequent job is run in
        # the same directory, will not restart this job.
        if os.path.exists("continue.json"):
            os.remove("continue.json")

    @classmethod
    def structure_optimization_run(cls,
                                   vasp_cmd: list,
                                   prev_structure_opt: StructureOptResult = None,
                                   gamma_vasp_cmd: Optional[list] = None,
                                   max_relax_num: int = 10,
                                   removes_wavecar: bool = False,
                                   std_out: str = "vasp.out",
                                   runshell: str = "runshell.sh",
                                   move_unimportant_files: bool = True,
                                   symprec=SYMMETRY_TOLERANCE,
                                   angle_tolerance=ANGLE_TOL) -> None:
        """Vasp job for structure optimization

        Args:
            vasp_cmd (list):
                Vasp command.
            gamma_vasp_cmd: (str/list/None)
                Gamma version of vasp command.
            max_relax_num (int):
                Maximum number of vasp calculations.
            removes_wavecar (bool):
                Whether to remove the WAVECAR files after geometry optimization.
            std_out (str):
                Name of the file showing the standard output.
            move_unimportant_files (bool):
                Whether to move relatively unimportant results to calc_results.

        Yield:
            ViseVaspJob class object.
        """
        backup_initial_input_files = True
        for job_number in range(1, max_relax_num + 1):
            yield cls(vasp_cmd=vasp_cmd,
                      gamma_vasp_cmd=gamma_vasp_cmd,
                      final=True,
                      backup=backup_initial_input_files,
                      suffix=f".{job_number}")
            shutil.copy(".".join(["CONTCAR", str(job_number)]), "POSCAR")
            backup_initial_input_files = False

            if len(Vasprun("vasprun.xml").ionic_steps) == 1:
                break
        else:
            raise VaspNotConvergedError("Structure optimization not converged")

        left_files = \
            VASP_INPUT_FILES | {"vise.json", "structure_opt.json", runshell}
        for f in VASP_SAVED_FILES | {std_out}:
            finish_name = f"{f}.finish"
            shutil.move(f"{f}.{job_number}", finish_name)
            left_files.add(finish_name)

        rm_wavecar(removes_wavecar)

        if move_unimportant_files:
            os.mkdir("files")

        result = StructureOptResult.from_dir(dir_name=".",
                                             symprec=symprec,
                                             angle_tolerance=angle_tolerance,
                                             prev_structure_opt=prev_structure_opt)

        result.to_json_file("structure_opt.json")

        for f in glob("*"):
            if not os.path.isfile(f):
                continue  # continue if f is directory.
            elif os.stat(f).st_size == 0:
                os.remove(f)  # remove empty files
            elif move_unimportant_files and f not in left_files:
                shutil.move(f, "files")

    @classmethod
    def kpt_converge(cls,
                     vasp_cmd: list,
                     xc: Xc = Xc.pbe,
                     user_incar_settings: Optional[dict] = None,
                     initial_kpt_density: Optional[float] = KPT_INIT_DENSITY,
                     gamma_vasp_cmd: Optional[list] = None,
                     max_relax_num: int = 10,
                     max_kpt_num: int = 10,
                     convergence_criterion: float = 0.003,
                     removes_wavecar: bool = False,
                     std_out: str = "vasp.out",
                     symprec=SYMMETRY_TOLERANCE,
                     angle_tolerance=ANGLE_TOL,
                     **kwargs) -> None:
        """"Vasp job for checking k-point convergence

        Args:
            vasp_cmd:
            gamma_vasp_cmd:
            max_relax_num:
            removes_wavecar:
            std_out:
                See docstrings of geom_opt_run.
            -------
            xc:
            user_incar_settings:
            initial_kpt_density:
                See docstrings of ViseInputSet.
            -------
            convergence_criterion:
                See docstrings of KptConvResult.
            -------
            max_kpt_num (str):
                Max k-point iteration number
            symprec (float):
                Distance precision in Angstrom used for symmetry analysis.
                This is also checked if the lattice constants are converged.
            angle_tolerance (float):
                Angle precision in degrees used for symmetry analysis.
            kwargs:
                Used for ViseInputSet keyword args.
        Return:
            None
        """
        vis_kwargs = kwargs or {}
        results = KptConvResult.from_dirs(convergence_criterion)
        is_sg_changed = results[-1].is_sg_changed if results else None

        while not results.conv_str_opt_result and len(results) < max_kpt_num:

            prev_str_opt = results[-1] if results else None

            vis_kwargs.update({"task": Task.structure_opt,
                               "xc": xc,
                               "user_incar_settings": user_incar_settings,
                               "symprec": symprec,
                               "angle_tolerance": angle_tolerance})

            if is_sg_changed is None or is_sg_changed is True:
                name = prev_str_opt.dirname + "/CONTCAR.finish" \
                    if is_sg_changed else "POSCAR"
                structure = Structure.from_file(name)
                kpt_density = initial_kpt_density
                vis = ViseInputSet.make_input(
                    structure=structure,
                    kpt_density=kpt_density,
                    **vis_kwargs)
            else:
                prev_kpt = prev_str_opt.num_kpt
                kpt_density = prev_str_opt.kpt_density

                # Iteratively increase kpt_density until num_kpt is incremented.
                while True:
                    kpt_density *= KPT_FACTOR
                    vis = ViseInputSet.from_prev_calc(
                        dirname=prev_str_opt.dirname,
                        parse_calc_results=False,
                        parse_incar=True,
                        sort_structure=False,
                        standardize_structure=True,
                        files_to_transfer={"WAVECAR": "m"},
                        contcar_filename="CONTCAR.finish",
                        kpt_density=kpt_density,
                        **vis_kwargs)
                    kpt = vis.kpoints.kpts[0]
                    if (not kpt == prev_kpt
                            and all([i >= j for i, j in zip(kpt, prev_kpt)])):
                        break

            vis.write_input(".")
            # Need to put forward the generator in structure_optimization_run
            for run in cls.structure_optimization_run(
                    vasp_cmd=vasp_cmd,
                    gamma_vasp_cmd=gamma_vasp_cmd,
                    max_relax_num=max_relax_num,
                    std_out=std_out,
                    symprec=symprec,
                    angle_tolerance=angle_tolerance,
                    prev_structure_opt=prev_str_opt):
                yield run

            str_opt = StructureOptResult.load_json("structure_opt.json")
            results.append(str_opt)
            print("dirname", str_opt.dirname)
            os.mkdir(str_opt.dirname)
            for filename in glob("*"):
                if filename == "files" or os.path.isfile(filename):
                    shutil.move(filename, str_opt.dirname)

            print("is_sg_changed", str_opt.is_sg_changed)
            is_sg_changed = str_opt.is_sg_changed

        rm_wavecar(remove_current=removes_wavecar, remove_subdirectories=True)

        if results.conv_str_opt_result:
            conv_dirname = Path(results.conv_str_opt_result.dirname)
            for filename in VASP_INPUT_FILES | VASP_FINISHED_FILES:
                os.symlink(str(conv_dirname / filename), filename)
#            print(results)
#            logger.log(results)
        else:
            raise KptNotConvergedError(
                "Energy was not converged w.r.t. the number of k-points ")


