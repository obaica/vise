# -*- coding: utf-8 -*-

from copy import deepcopy
from typing import Optional

import numpy as np
from pymatgen.core.structure import Structure
from pymatgen.io.vasp.sets import (
    VaspInputSet)
from vise.core.config import (
    KPT_DENSITY, ENCUT_FACTOR_STR_OPT, ANGLE_TOL, SYMPREC)
from vise.input_set.sets import (
    Task, Xc, TaskStructureKpoints, TaskIncarSettings)
from vise.util.logger import get_logger

logger = get_logger(__name__)

__author__ = "Yu Kumagai"
__maintainer__ = "Yu Kumagai"


class InputSet(VaspInputSet):
    def __init__(self,
                 structure: Structure,
                 orig_structure: Structure,
                 incar,
                 potcar,
                 kpoints,
                 task,
                 xc,
                 kpt_mode,
                 kpt_density,
                 band_ref_dist: float,
                 factor,
                 encut,
                 structure_opt_encut_factor: float,
                 is_magnetization: bool,
                 hubbard_u: Optional[bool],
                 ldauu: Optional[dict],
                 ldaul: Optional[dict],
                 **kwargs):
        pass

    @classmethod
    def make_input(cls,
                   xc: Xc = Xc.pbe,
                   task: Task = Task.structure_opt,
                   prev_set: "InputSet" = None,
                   structure: Structure = None,
                   user_incar_settings: dict = None,
                   standardize_structure: bool = True,
                   sort_structure: bool = True,
                   band_gap: float = None,
                   vbm_cbm: list = None,
                   factor: int = None,
                   encut: float = None,
                   structure_opt_encut_factor: float = ENCUT_FACTOR_STR_OPT,
                   hubbard_u: bool = True,
                   is_magnetization: bool = False,
                   kpt_mode: str = "primitive_uniform",
                   kpt_density: float = KPT_DENSITY,
                   kpts_shift: list = None,
                   band_ref_dist: float = 0.03,
                   ldauu: dict = None,  # consider later
                   ldaul: dict = None,  # consider later
                   npar_kpar: bool = True,
                   num_cores=None,
                   default_potcar: str = None,
                   override_potcar_set: dict = None,
                   files_to_transfer: Optional[dict] = None,
                   symprec: float = SYMPREC,
                   angle_tolerance: float = ANGLE_TOL):

        structure = deepcopy(structure)
        num_cores = num_cores or [36, 1]
        files_to_transfer = files_to_transfer or {}
        user_incar_settings = user_incar_settings or {}

        if prev_set:
            original_structure = prev_set.structure
        else:
            if structure:
                original_structure = structure
            else:
                raise ValueError

        if prev_set and prev_set.task == task and original_structure == structure:
            str_kpt = prev_set.str_kpt
            task_set = prev_set.task_set
        else:
            str_kpt = TaskStructureKpoints.from_options(task=task,
                                                        original_structure=structure,
                                                        )
            task_set = TaskIncarSettings.from_options(task=task,
                                                      num_kpoints=str_kpt.num_kpts,
                                                      is_magnetization=is_magnetization,
                                                      band_gap=band_gap,
                                                      vbm_cbm=vbm_cbm,
                                                      npar_kpar=npar_kpar)

        if prev_set and prev_set.xc == xc:
            xc_set = prev_set.xc_set
        else:
            xc_set = XcInputSet.from_options(xc=1,
                                             structure=structure)

        if prev_set and prev_set.task == task and prev_set.xc == xc:
            task_xc_set = prev_set.task_xc_set
        else:
            task_xc_set = TaskXcInputSet.from_options(xc=1,
                                                      structure=structure)

        if prev_set:
            common_set = prev_set.common_set
        else:
            common_set = CommonSet.from_options()

        structure_changed = not np.allclose(
            original_structure.lattice.matrix,
            structure.lattice.matrix, atol=symprec)

        if structure_changed:
            # The following files are useless when the lattice is changed.
            for f in ["CHGCAR", "WAVECAR"]:
                files_to_transfer.pop(f, None)

        incar = (task_set.incar_setting + xc_set.incar_setting +
                 task_xc_set.incar_setting + common_set.incar_setting +
                 user_incar_settings)

