# -*- coding: utf-8 -*-

from monty.os.path import zpath
import os
from collections import Counter
import subprocess

from custodian.custodian import ErrorHandler
from custodian.utils import backup
from custodian.vasp.handlers import VASP_BACKUP_FILES, UnconvergedErrorHandler, NonConvergingErrorHandler

from pymatgen.transformations.standard_transformations import \
    SupercellTransformation
from pymatgen.io.vasp import VaspInput, Incar, Kpoints, Vasprun, \
    Oszicar, Outcar

from obadb.custodian.oba_vaspjob import ObaVaspModder


__author__ = "Yu Kumagai"
__maintainer__ = "Yu Kumagai"


class ObaVaspErrorHandler(ErrorHandler):
    """
    Master VaspErrorHandler class that handles a number of common errors
    that occur during VASP runs.
    """

    is_monitor = True

    error_msgs = {
        "tet":             ["Tetrahedron method fails for NKPT<4",
                            "Fatal error detecting k-mesh",
                            "Fatal error: unable to match k-point",
                            "Routine TETIRR needs special values"],
        "inv_rot_mat":     [
            "inverse of rotation matrix was not found (increase "
            "SYMPREC)"],
        #        "brmix": ["BRMIX: very serious problems"],
        "subspacematrix":  ["WARNING: Sub-Space-Matrix is not hermitian in "
                            "DAV"],
        "tetirr":          ["Routine TETIRR needs special values"],
        "incorrect_shift": ["Could not get correct shifts"],
        "real_optlay":     ["REAL_OPTLAY: internal error",
                            "REAL_OPT: internal ERROR"],
        "rspher":          ["ERROR RSPHER"],
        "dentet":          ["DENTET"],
        "too_few_bands":   ["TOO FEW BANDS"],
        "triple_product":  ["ERROR: the triple product of the basis vectors"],
        "rot_matrix":      [
            "Found some non-integer element in rotation matrix"],
        "brions":          ["BRIONS problems: POTIM should be increased"],
        "pricel":          ["internal error in subroutine PRICEL"],
        "zpotrf":          ["LAPACK: Routine ZPOTRF failed"],
        "amin":            [
            "One of the lattice vectors is very long (>50 A), but AMIN"],
        "zbrent":          ["ZBRENT: fatal internal in",
                            "ZBRENT: fatal error in bracketing"],
        "pssyevx":         ["ERROR in subspace rotation PSSYEVX"],
        "eddrmm":          ["WARNING in EDDRMM: call to ZHEGV failed"],
        "edddav":          ["Error EDDDAV: Call to ZHEGV failed"],
        "grad_not_orth":   [
            "EDWAV: internal error, the gradient is not orthogonal"],
        "nicht_konv":      ["ERROR: SBESSELITER : nicht konvergent"],
        "zheev":           ["ERROR EDDIAG: Call to routine ZHEEV failed!"],
        "elf_kpar":        ["ELF: KPAR>1 not implemented"],
        "elf_ncl":         [
            "WARNING: ELF not implemented for non collinear case"],
        "rhosyg":          ["RHOSYG internal error"],
        "posmap":          [
            "POSMAP internal error: symmetry equivalent atom not found"],
        "plane_wave_coeff":
            ["ERROR: while reading WAVECAR, plane wave coefficients changed"],
        "point_group": ["Error: point group operation missing"]
    }

    def __init__(self, output_filename="vasp.out", natoms_large_cell=50,
                 errors_subset_to_catch=None):
        """
        Initializes the handler with the output file to check.

        Args:
            output_filename (str): This is the file where the stdout for vasp
                is being redirected. The error messages that are checked are
                present in the stdout. Defaults to "vasp.out", which is the
                default redirect used by :class:`custodian.vasp.jobs.VaspJob`.
            natoms_large_cell (int): Number of atoms threshold to treat cell
                as large. Affects the correction of certain errors. Defaults to
                100.
            errors_subset_to_detect (list): A subset of errors to catch. The
                default is None, which means all supported errors are detected.
                Use this to only catch only a subset of supported errors.
                E.g., ["eddrrm", "zheev"] will only catch the eddrmm and zheev
                errors, and not others. If you wish to only excluded one or
                two of the errors, you can create this list by the following
                lines:

                ```
                subset = list(VaspErrorHandler.error_msgs.keys())
                subset.pop("eddrrm")

                handler = VaspErrorHandler(errors_subset_to_catch=subset)
                ```
        """
        self.output_filename = output_filename
        self.errors = set()
        self.error_count = Counter()
        # threshold of number of atoms to treat the cell as large.
        self.natoms_large_cell = natoms_large_cell
        self.errors_subset_to_catch = errors_subset_to_catch or \
                                      list(
                                          ObaVaspErrorHandler.error_msgs.keys())

    def check(self):
        incar = Incar.from_file("INCAR")
        self.errors = set()
        with open(self.output_filename, "r") as f:
            for line in f:
                l = line.strip()
                for err, msgs in ObaVaspErrorHandler.error_msgs.items():
                    if err in self.errors_subset_to_catch:
                        for msg in msgs:
                            if l.find(msg) != -1:
                                # this checks if we want to run a charged
                                # computation (e.g., defects) if yes we don't
                                # want to kill it because there is a change in
                                # e-density (brmix error)
                                if err == "brmix" and 'NELECT' in incar:
                                    continue
                                self.errors.add(err)
        return len(self.errors) > 0

    def correct(self):
        backup(VASP_BACKUP_FILES | {self.output_filename})
        actions = []
        vi = VaspInput.from_directory(".")

        if self.errors.intersection(["tet", "dentet"]):
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"ISMEAR": 0}}})

        if "inv_rot_mat" in self.errors:
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"SYMPREC": 1e-8}}})

        # Oba-Group original
        if "plane_wave_coeff" in self.errors:
            actions.append({"file": "WAVECAR",
                            "action": {
                                "_file_delete": {'mode': "actual"}}})
            actions.append({"file": "CHGCAR",
                            "action": {
                                "_file_delete": {'mode': "actual"}}})

        # if "brmix" in self.errors:
        #     # If there is not a valid OUTCAR already, increment
        #     # error count to 1 to skip first fix
        #     if self.error_count['brmix'] == 0:
        #         try:
        #             assert (Outcar(zpath(os.path.join(
        #                 os.getcwd(), "OUTCAR"))).is_stopped is False)
        #         except:
        #             self.error_count['brmix'] += 1

        # if self.error_count['brmix'] == 0:
        #     # Valid OUTCAR - simply rerun the job and increment
        #     # error count for next time
        #     actions.append({"dict": "INCAR",
        #                     "action": {"_set": {"ISTART": 1}}})
        #     self.error_count['brmix'] += 1

        # elif self.error_count['brmix'] == 1:
        #     # Use Kerker mixing w/default values for other parameters
        #     actions.append({"dict": "INCAR",
        #                     "action": {"_set": {"IMIX": 1}}})
        #     self.error_count['brmix'] += 1

        # elif self.error_count['brmix'] == 2 and vi["KPOINTS"].style \
        #         == Kpoints.supported_modes.Gamma:
        #     actions.append({"dict": "KPOINTS",
        #                     "action": {"_set": {"generation_style":
        #                                             "Monkhorst"}}})
        #     actions.append({"dict": "INCAR",
        #                     "action": {"_unset": {"IMIX": 1}}})
        #     self.error_count['brmix'] += 1

        # elif self.error_count['brmix'] in [2, 3] and vi["KPOINTS"].style \
        #         == Kpoints.supported_modes.Monkhorst:
        #     actions.append({"dict": "KPOINTS",
        #                     "action": {"_set": {"generation_style":
        #                                             "Gamma"}}})
        #     actions.append({"dict": "INCAR",
        #                     "action": {"_unset": {"IMIX": 1}}})
        #     self.error_count['brmix'] += 1

        # if vi["KPOINTS"].num_kpts < 1:
        #     all_kpts_even = all([
        #         bool(n % 2 == 0) for n in vi["KPOINTS"].kpts[0]
        #     ])
        #     print("all_kpts_even = {}".format(all_kpts_even))
        #     if all_kpts_even:
        #         new_kpts = (
        #             tuple(n + 1 for n in vi["KPOINTS"].kpts[0]),)
        #         print("new_kpts = {}".format(new_kpts))
        #         actions.append({"dict": "KPOINTS", "action": {"_set": {
        #             "kpoints": new_kpts
        #         }}})

        # else:
        #     actions.append({"dict": "INCAR",
        #                     "action": {"_set": {"ISYM": 0}}})

        # if vi["KPOINTS"].style == Kpoints.supported_modes.Monkhorst:
        #     actions.append({"dict": "KPOINTS",
        #                     "action": {
        #                         "_set": {"generation_style": "Gamma"}}})

        # # Based on VASP forum's recommendation, you should delete the
        # # CHGCAR and WAVECAR when dealing with this error.
        # if vi["INCAR"].get("ICHARG", 0) < 10:
        #     actions.append({"file": "CHGCAR",
        #                     "action": {
        #                         "_file_delete": {'mode': "actual"}}})
        #     actions.append({"file": "WAVECAR",
        #                     "action": {
        #                         "_file_delete": {'mode': "actual"}}})

        if "zpotrf" in self.errors:
            # Usually caused by short bond distances. If on the first step,
            # volume needs to be increased. Otherwise, it was due to a step
            # being too big and POTIM should be decreased.  If a static run
            # try turning off symmetry.
            try:
                oszicar = Oszicar("OSZICAR")
                nsteps = len(oszicar.ionic_steps)
            except:
                nsteps = 0

            if nsteps >= 1:
                potim = float(vi["INCAR"].get("POTIM", 0.5)) / 2.0
                actions.append(
                    {"dict":   "INCAR",
                     "action": {"_set": {"ISYM": 0, "POTIM": potim}}})
            elif vi["INCAR"].get("NSW", 0) == 0 \
                    or vi["INCAR"].get("ISIF", 0) in range(3):
                actions.append(
                    {"dict": "INCAR", "action": {"_set": {"ISYM": 0}}})
            else:
                s = vi["POSCAR"].structure
                s.apply_strain(0.2)
                actions.append({"dict":   "POSCAR",
                                "action": {"_set": {"structure": s.as_dict()}}})

            # Based on VASP forum's recommendation, you should delete the
            # CHGCAR and WAVECAR when dealing with this error.
            if vi["INCAR"].get("ICHARG", 0) < 10:
                actions.append({"file":   "CHGCAR",
                                "action": {"_file_delete": {'mode': "actual"}}})
                actions.append({"file":   "WAVECAR",
                                "action": {"_file_delete": {'mode': "actual"}}})

        if self.errors.intersection(["subspacematrix"]):
            if self.error_count["subspacematrix"] == 0:
                actions.append({"dict":   "INCAR",
                                "action": {"_set": {"LREAL": False}}})
            else:
                actions.append({"dict":   "INCAR",
                                "action": {"_set": {"PREC": "Accurate"}}})
            self.error_count["subspacematrix"] += 1

        if self.errors.intersection(["rspher", "real_optlay", "nicht_konv"]):
            s = vi["POSCAR"].structure
            if len(s) < self.natoms_large_cell:
                actions.append({"dict":   "INCAR",
                                "action": {"_set": {"LREAL": False}}})
            else:
                # for large supercell, try an in-between option LREAL = True
                # prior to LREAL = False
                if self.error_count['real_optlay'] == 0:
                    # use real space projectors generated by pot
                    actions.append({"dict":   "INCAR",
                                    "action": {"_set": {"LREAL": True}}})
                elif self.error_count['real_optlay'] == 1:
                    actions.append({"dict":   "INCAR",
                                    "action": {"_set": {"LREAL": False}}})
                self.error_count['real_optlay'] += 1

        if self.errors.intersection(["tetirr", "incorrect_shift"]):

            if vi["KPOINTS"].style == Kpoints.supported_modes.Monkhorst or \
                    vi["KPOINTS"].kpts_shift != [0.0, 0.0, 0.0]:
                actions.append({"dict":   "KPOINTS",
                                "action": {
                                    "_set": {"generation_style": "Gamma",
                                             "usershift": [0.0, 0.0, 0.0]}}})
            # if vi["KPOINTS"].style == Kpoints.supported_modes.Monkhorst:
            #     actions.append({"dict":   "KPOINTS",
            #                     "action": {

        if "rot_matrix" in self.errors:
            if vi["KPOINTS"].style == Kpoints.supported_modes.Monkhorst or \
                    vi["KPOINTS"].kpts_shift != [0.0, 0.0, 0.0]:
                actions.append({"dict":   "KPOINTS",
                                "action": {
                                    "_set": {"generation_style": "Gamma",
                                             "usershift": [0.0, 0.0, 0.0]}}})
            else:
                actions.append({"dict":   "INCAR",
                                "action": {"_set": {"ISYM": 0}}})

        if "amin" in self.errors:
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"AMIN": "0.01"}}})

        if "triple_product" in self.errors:
            s = vi["POSCAR"].structure
            trans = SupercellTransformation(((1, 0, 0), (0, 0, 1), (0, 1, 0)))
            new_s = trans.apply_transformation(s)
            actions.append({"dict":           "POSCAR",
                            "action":         {
                                "_set": {"structure": new_s.as_dict()}},
                            "transformation": trans.as_dict()})

        if "pricel" in self.errors:
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"SYMPREC": 1e-8, "ISYM": 0}}})

        if "brions" in self.errors:
            potim = float(vi["INCAR"].get("POTIM", 0.5)) + 0.1
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"POTIM": potim}}})

        if "zbrent" in self.errors:
            # Modified so as not to use IBRION=1 as it does not show the
            # eigenvalues in vasprun.xml >>>>>>>>>>>>
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"ADDGRID": True}}})
            actions.append({"file":   "CONTCAR",
                            "action": {"_file_copy": {"dest": "POSCAR"}}})
        #            actions.append({"dict": "INCAR",
        #                            "action": {"_set": {"IBRION": 1}}})
        #            actions.append({"file": "CONTCAR",
        #                            "action": {"_file_copy": {"dest": "POSCAR"}}})
        # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

        if "too_few_bands" in self.errors:
            if "NBANDS" in vi["INCAR"]:
                nbands = int(vi["INCAR"]["NBANDS"])
            else:
                with open("OUTCAR") as f:
                    for line in f:
                        if "NBANDS" in line:
                            try:
                                d = line.split("=")
                                nbands = int(d[-1].strip())
                                break
                            except (IndexError, ValueError):
                                pass
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"NBANDS": int(1.1 * nbands)}}})

        if "pssyevx" in self.errors:
            actions.append({"dict": "INCAR", "action":
                                    {"_set": {"ALGO": "Normal"}}})
        if "eddrmm" in self.errors:
            # RMM algorithm is not stable for this calculation
            if vi["INCAR"].get("ALGO", "Normal") in ["Fast", "VeryFast"]:
                actions.append({"dict": "INCAR", "action":
                                        {"_set": {"ALGO": "Normal"}}})
            else:
                potim = float(vi["INCAR"].get("POTIM", 0.5)) / 2.0
                actions.append({"dict":   "INCAR",
                                "action": {"_set": {"POTIM": potim}}})
            if vi["INCAR"].get("ICHARG", 0) < 10:
                actions.append({"file":   "CHGCAR",
                                "action": {"_file_delete": {'mode': "actual"}}})
                actions.append({"file":   "WAVECAR",
                                "action": {"_file_delete": {'mode': "actual"}}})

        if "edddav" in self.errors:
            if vi["INCAR"].get("ICHARG", 0) < 10:
                actions.append({"file":   "CHGCAR",
                                "action": {"_file_delete": {'mode': "actual"}}})
            actions.append({"dict": "INCAR", "action":
                                    {"_set": {"ALGO": "All"}}})

        if "grad_not_orth" in self.errors:
            if vi["INCAR"].get("ISMEAR", 1) < 0:
                actions.append({"dict":   "INCAR",
                                "action": {"_set": {"ISMEAR": "0"}}})

        if "zheev" in self.errors:
            if vi["INCAR"].get("ALGO", "Fast").lower() != "exact":
                actions.append({"dict":   "INCAR",
                                "action": {"_set": {"ALGO": "Exact"}}})
        if "elf_kpar" in self.errors:
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"KPAR": 1}}})

        if "rhosyg" in self.errors:
            if vi["INCAR"].get("SYMPREC", 1e-4) == 1e-4:
                actions.append({"dict":   "INCAR",
                                "action": {"_set": {"ISYM": 0}}})
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"SYMPREC": 1e-4}}})

        if "posmap" in self.errors:
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"SYMPREC": 1e-6}}})

        if "point_group" in self.errors:
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"ISYM": 0}}})

        ObaVaspModder(vi=vi).apply_actions(actions)
        return {"errors": list(self.errors), "actions": actions}


class ObaUnconvergedErrorHandler(UnconvergedErrorHandler):

    def correct(self):
        backup(VASP_BACKUP_FILES)
        v = Vasprun(self.output_filename)
        actions = [{"file":   "CONTCAR",
                    "action": {"_file_copy": {"dest": "POSCAR"}}}]
        if not v.converged_electronic:
            # For SCAN try switching to CG for the electronic minimization
            if "SCAN" in v.incar.get("METAGGA", "").upper():
                new_settings = {"ALGO": "All"}
            else:
                new_settings = {"ISTART":   1,
                                "ALGO":     "Normal",
                                "NELMDL":   -6,
                                "BMIX":     0.001,
                                "AMIX_MAG": 0.8,
                                "BMIX_MAG": 0.001}

            if all([v.incar.get(k, "") == val for k, val in
                    new_settings.items()]):
                return {"errors": ["Unconverged"], "actions": None}

            actions.append({"dict":   "INCAR",
                            "action": {"_set": new_settings}})

        # Instead of changing the IBRION, we reduce the EDIFF since we usually
        # use the constant EDIFF value as default.
        if not v.converged_ionic:
            ediff = v.incar["EDIFF"]
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"EDIFF": ediff * 0.5}}})
            actions.append({"dict":   "INCAR",
                            "action": {"_set": {"ADDGRID": True}}})
        ObaVaspModder().apply_actions(actions)
        return {"errors": ["Unconverged"], "actions": actions}


class ObaDielectricMaxIterationErrorHandler(ErrorHandler):

    is_monitor = True

    def __init__(self, oszicar="OSZICAR", incar="INCAR"):
        self.error_count = Counter()
        self.oszicar = oszicar
        self.incar = incar

    def check(self):

        incar = Incar.from_file(self.incar)
        nelm = incar.get("NELM", 60)
        unconverged_line = ':' + "{:4d}".format(nelm)
        with open(self.oszicar, "r") as fr:
            return any(unconverged_line in line for line in fr)

    def correct(self):
        # actions = []
        # vi = VaspInput.from_directory(".")

        # if self.error_count == 0:
        #     actions.append({"dict":   "INCAR",
        #                     "action": {"_set": {"NELM": self.nelm * 2}}})
        #     ObaVaspModder(vi=vi).apply_actions(actions)
        #     return {"errors": list(self.errors), "actions": actions}

        # else:
        #     return {"errors": ["No_DFPT_convergence"], "actions": None}
        return {"errors": ["No_DFPT_convergence"], "actions": None}


class ObaReturnErrorHandler(ErrorHandler):
    is_monitor = True
    def __init__(self):
        pass

    def check(self):
        return True

    def correct(self):
        # Unfixable error. Just return None for actions.
        return {"errors": ["Always return Error with this."], "actions": None}


class ObaMemorySwapHandler(ErrorHandler):
    """
    Detects if the memory is overflowed.
    """

    is_monitor = True

    def __init__(self, memory_usage_limit=0.85):
        """
        Initializes the handler with the output file to check.

        Args:
            memory_usage_limit (float):
        """
        self.memory_usage_limit = memory_usage_limit

    def check(self):
        res = subprocess.check_output('free').split()
        memory_usage = int(res[8]) / int(res[7])
        if memory_usage > self.memory_usage_limit:
            return True

    def correct(self):
        # Unfixable error. Just return None for actions.
        return {"errors": ["Too_much_memory_usage"], "actions": None}


class ObaTooLongTimeCalcErrorHandler(ErrorHandler):
    """
    Detects if the memory is overflowed.
    """

    is_monitor = True

    def __init__(self, timeout=129600):
        """

        60 * 60 * 36 = 129600

        Args:
            limited_time (int):
        """
        self.timeout = timeout

    def check(self):
        now_time = int(subprocess.check_output(['date', '+%s']))
        incar_time = int(subprocess.check_output(['date', '+%s', '-r', 'INCAR']))
        incar_age = now_time - incar_time
        if incar_age > self.timeout:
            return True

    def correct(self):
        # Unfixable error. Just return None for actions.
        return {"errors": ["Too_long_calc"], "actions": None}


class ObaNonConvergingErrorHandler(NonConvergingErrorHandler):

    def __init__(self, output_filename="OSZICAR", incar="INCAR",
                 change_algo=False):
        """
        Initializes the handler with the output file to check.

        Args:
            output_filename (str): This is the OSZICAR file. Change
                this only if it is different from the default (unlikely).
            nionic_steps (int): The threshold number of ionic steps that
                needs to hit the maximum number of electronic steps for the
                run to be considered non-converging.
            change_algo (bool): Whether to attempt to correct the job by
                changing the ALGO from Fast to Normal.
        """
        self.output_filename = output_filename
        self.incar = incar
        self.change_algo = change_algo

    def check(self):
        incar = Incar.from_file(self.incar)
        nionic_steps = incar.get("NELM", 1)
        nelm = incar.get("NELM", 60)
        try:
            oszicar = Oszicar(self.output_filename)
            esteps = oszicar.electronic_steps
            if len(esteps) > nionic_steps:
                return all([len(e) == nelm
                            for e in esteps[-(nionic_steps + 1):-1]])
        except:
            pass
        return False


class ObaDivergingEnergyErrorHandler(ErrorHandler):

    def __init__(self, output_filename="OSZICAR"):
        """
        Initializes the handler with the output file to check.

        Args:
            output_filename (str): This is the OSZICAR file. Change
                this only if it is different from the default (unlikely).
            nionic_steps (int): The threshold number of ionic steps that
                needs to hit the maximum number of electronic steps for the
                run to be considered non-converging.
            change_algo (bool): Whether to attempt to correct the job by
                changing the ALGO from Fast to Normal.
        """
        self.output_filename = output_filename

    def check(self):
        oszicar = Oszicar(self.output_filename)
        esteps = oszicar.electronic_steps
        # OSZICAR file can be empty, thus we needs try-except here.
        try:
            max_energy = max([es["E"] for es in esteps[-1]])
        except:
            return False

        if max_energy > 10 ** 6:
            return True

    def correct(self):
        # Unfixable error. Just return None for actions.
        return {"errors": ["Energy_diverging"], "actions": None}


