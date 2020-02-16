#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from distutils.util import strtobool
from typing import Union

from vise.config import SYMMETRY_TOLERANCE, ANGLE_TOL, KPT_DENSITY
from vise.custodian_extension.jobs import ViseVaspJob
from vise.main_function import (
    get_poscar_from_mp, vasp_set, chempotdiag, plot_band, plot_dos, vasp_run,
    band_gap)
from vise.util.logger import get_logger
from vise.util.main_tools import dict2list, get_user_settings, get_default_args
from vise.util.mp_tools import make_poscars_from_mp
from vise.input_set.input_set import ViseInputSet

__author__ = "Yu Kumagai"
__maintainer__ = "Yu Kumagai"

logger = get_logger(__name__)

__version__ = '0.0.1dev'
__date__ = 'will be inserted'

# The following keys are set by vise.yaml
setting_keys = ["vasp_cmd",
                "symprec",
                "angle_tolerance",
                "xc",
                "kpt_density",
                "initial_kpt_density",
                "vise_opts",
                "user_incar_setting",
                "ldauu",
                "ldaul",
                "potcar_set",
                "potcar_set_name",
                "relax_iter_num",
                "removed_files"]

user_settings = get_user_settings(yaml_filename="vise.yaml",
                                  setting_keys=setting_keys)


def main():

    def simple_override(d: dict, keys: Union[list, str]) -> None:
        """Override dict if keys exist in vise.yaml.

        When the value in the user_settings is a dict, it will be changed to
        list using dict2list.
        """
        if isinstance(keys, str):
            keys = [keys]
        for key in keys:
            if key in user_settings:
                v = user_settings[key]
                if isinstance(v, dict):
                    v = dict2list(v)
                d[key] = v

    parser = argparse.ArgumentParser(
        description="""                            
    Vise is a package that helps researchers to do first-principles calculations 
    with the VASP code.""",
        epilog=f"""                                 
    Author: Yu Kumagai
    Version: {__version__}                                                                 
    Last updated: {__date__}""",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    subparsers = parser.add_subparsers()

    # -- parent parser: prec
    prec = {"symprec": SYMMETRY_TOLERANCE,
            "angle_tolerance": ANGLE_TOL}
    simple_override(prec, ["symprec", "angle_tolerance"])

    prec_parser = argparse.ArgumentParser(description="Prec-related parser",
                                          add_help=False)
    prec_parser.add_argument(
        "--symprec", type=float, default=prec["symprec"],
        help="Set length precision used for symmetry analysis [A].")
    prec_parser.add_argument(
        "--angle_tolerance", type=float, default=prec["angle_tolerance"],
        help="Set angle precision used for symmetry analysis.")

    # -- parent parser:  vasp set
    vasp_defaults = get_default_args(ViseInputSet.make_input)
    vasp_defaults.update(ViseInputSet.TASK_OPTIONS)
    vasp_defaults.update(ViseInputSet.XC_OPTIONS)
    vasp_defaults["potcar_set"] = None
    vasp_defaults["vise_opts"] = None
    vasp_defaults["user_incar_setting"] = None
    simple_override(vasp_defaults, ["xc",
                                    "task",
                                    "vise_opts",
                                    "user_incar_setting",
                                    "potcar_set",
                                    "potcar_set_name",
                                    "ldauu",
                                    "ldaul"])

    vasp_parser = argparse.ArgumentParser(description="Vasp set-related parser",
                                          add_help=False)

    vasp_parser.add_argument(
        "--potcar", dest="potcar_set", default=vasp_defaults["potcar_set"],
        type=str, nargs="+",
        help="User specifying POTCAR set. E.g., Mg_pv O_h")
    vasp_parser.add_argument(
        "--potcar_set_name", default=vasp_defaults["potcar_set_name"], type=str,
        nargs="+", help="User specifying POTCAR set name, i.e., normal ,gw, or "
             "mp_relax_set.")
    vasp_parser.add_argument(
        "-x", "--xc", default=str(vasp_defaults["xc"]), type=str,
        help="Exchange-correlation (XC) interaction treatment.")
    vasp_parser.add_argument(
        "-t", "--task", default=str(vasp_defaults["task"]), type=str,
        help="The task name. See document of vise.")
    vasp_parser.add_argument(
        "--vise_opts", type=str, nargs="+", default=vasp_defaults["vise_opts"],
        help="Keyword arguments for options in make_input classmethod of "
             "ViseInputSet in vise. See document in vise for details.")
    vasp_parser.add_argument(
        "-uis", "--user_incar_setting", type=str, nargs="+",
        default=vasp_defaults["user_incar_setting"],
        help="user_incar_setting in make_input classmethod of ViseInputSet in "
             "vise. See document in vise for details.")
    vasp_parser.add_argument(
        "-auis", "--additional_user_incar_setting", type=str, nargs="+",
        default=None,
        help="Use this if one does not want to override user_incar_setting "
             "written in the yaml file")
    vasp_parser.add_argument(
        "-ldauu", type=str, default=vasp_defaults["ldauu"], nargs="+",
        help="Dict of LDAUU values")
    vasp_parser.add_argument(
        "-ldaul", type=str, default=vasp_defaults["ldaul"], nargs="+",
        help="Dict of LDAUL values.")

    # -- get_poscars -----------------------------------------------------------
    parser_get_poscar = subparsers.add_parser(
        name="get_poscars",
        description="Tools for generating POSCAR file(s)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        aliases=['gp'])

    gp_defaults = get_default_args(make_poscars_from_mp)

    parser_get_poscar.add_argument(
        "-p", "--poscar", type=str,
        help="POSCAR-type file name.", metavar="FILE")
    parser_get_poscar.add_argument(
        "-n", "--number", type=int,
        help="MP entry number without prefix 'mp-'")
    parser_get_poscar.add_argument(
        "-e", "--elements", type=str, nargs="+",
        help="Create directories with POSCARs containing the input elements.")
    parser_get_poscar.add_argument(
        "--e_above_hull", type=float, default=gp_defaults["e_above_hull"],
        help="Collect materials with this hull energy in eV/atom.")
    parser_get_poscar.add_argument(
        "--molecules", type=strtobool, default=gp_defaults["molecules"],
        help="Whether one doesn't want to replace pmg structures to molecules.")

    parser_get_poscar.set_defaults(func=get_poscar_from_mp)

    # -- vasp_set ---------------------------------------------------------
    parser_vasp_set = subparsers.add_parser(
        name="vasp_set",
        parents=[vasp_parser, prec_parser],
        description="Tools for constructing vasp input set with vise",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        aliases=['vs'])

    vs_defaults = {"kpt_density": KPT_DENSITY}
    simple_override(vs_defaults, ["kpt_density"])

    parser_vasp_set.add_argument(
        "--pj", dest="print_json", type=str,
        help="Print the ViseInputSet info from the given json file.")
    parser_vasp_set.add_argument(
        "-p", "--poscar", default="POSCAR", type=str,
        help="POSCAR-type file name.")
    parser_vasp_set.add_argument(
        "-k", "--kpt_density", default=vs_defaults["kpt_density"], type=float,
        help="K-point density in Angstrom along each direction .")
    parser_vasp_set.add_argument(
        "-s", "--standardize", action="store_false",
        help="Store if one doesn't want the cell to be transformed to a "
             "primitive cell.")
    parser_vasp_set.add_argument(
        "-d", "--prev_dir", type=str,
        help="Inherit input files from the previous directory.")
    parser_vasp_set.add_argument(
        "-c", "--charge", type=int, default=0, help="Supercell charge state.")
    parser_vasp_set.add_argument(
        "--dirs", nargs="+", type=str, default=None,
        help="Make vasp set for the directories in the same condition.")
    parser_vasp_set.add_argument(
        "-npi", "--no_prior_info", dest="prior_info", action="store_false",
        help="Set if prior_info.json is *not* read although it exists.")

    del vs_defaults

    parser_vasp_set.set_defaults(func=vasp_set)

    # -- vasp_run --------------------------------------------------------------
    parser_vasp_run = subparsers.add_parser(
        name="vasp_run",
        parents=[vasp_parser, prec_parser],
        description="Tools for vasp run",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        aliases=['vr'])

    vr_defaults = get_default_args(ViseVaspJob.kpt_converge)
    vr_defaults["vasp_cmd"] = None

    simple_override(vr_defaults, ["vasp_cmd",
                                  "initial_kpt_density"
                                  "relax_iter_num",
                                  "convergence_criterion"])

    parser_vasp_run.add_argument(
        "--print", action="store_true",
        help="Print the structure_opt.json or kpt_conv.json (if -kc is set) "
             "info.")
    parser_vasp_run.add_argument(
        "--json_file", default=None, type=str, help="Json file name.")
    parser_vasp_run.add_argument(
        "-v", "--vasp_cmd", nargs="+", type=str,
        default=vr_defaults["vasp_cmd"],
        help="VASP command. If you are using mpirun, set this to something "
             "like \"mpirun pvasp\".",)
    parser_vasp_run.add_argument(
        "-ikd", "-initial_kpt_density", type=float,
        default=vr_defaults["initial_kpt_density"],
        help="Initial k-point density.")
    parser_vasp_run.add_argument(
        "-handler_name", type=str, default="default",
        help="Custodian error handler name listed in error_handlers.")
    parser_vasp_run.add_argument(
        "-timeout", type=int, default=518400,
        help="Timeout used in TooLongTimeCalcErrorHandler.")
    parser_vasp_run.add_argument(
        "--remove_wavecar", dest="rm_wavecar", action="store_true",
        help="Remove WAVECAR file after the calculation is finished.")
    parser_vasp_run.add_argument(
        "--max_relax_num", default=vr_defaults["max_relax_num"], type=int,
        help="Maximum number of relaxations.")
    parser_vasp_run.add_argument(
        "-criteria", dest="convergence_criterion",
        default=vr_defaults["convergence_criterion"], type=float,
        help="Convergence criterion of kpoints in eV / atom.")
    parser_vasp_run.add_argument(
        "--left_files", type=str, nargs="+", default=vr_defaults["left_files"],
        help="Filenames that are left at the calculation directory.")
    parser_vasp_run.add_argument(
        "-kc", "--kpoint_conv", action="store_true",
        help="Set if k-point convergence is checked.")

    del vr_defaults
    parser_vasp_run.set_defaults(func=vasp_run)

    # -- chempotdiag -----------------------------------------------------------
    parser_cpd = subparsers.add_parser(
        name="chempotdiag",
        description="Tools for chemical potentials",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        aliases=['cpd'])

    # input
    # from file
    parser_cpd.add_argument(
        "-e", "--energy", dest="energy_csv", type=str, default=None,
        help="Name of csv file of energies of compounds")
    parser_cpd.add_argument(
        "-v", "--vasp_dirs", dest="vasp_dirs", type=str, nargs='+',
        default=None,
        help="Drawing diagram from specified directories of vasp calculations")
    # from VASP and MP
    parser_cpd.add_argument("-fmp_target", "--from_mp_target",
                        help="VASP result of target material,"
                             "when get competing phases from mp")

    parser_cpd.add_argument("-fmp_elem", "--from_mp_element", nargs="*",
                        help="VASP result of elements,"
                             "when get competing phases from mp")

    # VASP_option
    parser_cpd.add_argument("-p", "--poscar_name",
                        dest="poscar_name", type=str,
                        default="POSCAR",
                        help="Name of POSCAR, like CONTCAR, "
                             "POSCAR-finish,...")
    parser_cpd.add_argument("-o", "--outcar_name",
                        dest="outcar_name", type=str,
                        default="OUTCAR",
                        help="Name of OUTCAR, like OUTCAR-finish")

    parser_cpd.add_argument("-es", "--energy_shift", type=str,
                        dest="energy_shift",
                        nargs='+', default=None,
                        help="Energy shift, "
                             "e.g. -es N2/molecule 1 "
                             "-> make more unstable N2/molecule "
                             "by 1 eV")

    # thermodynamic status (P and T) input
    parser_cpd.add_argument("-pp", "--partial_pressures",
                        dest="partial_pressures", type=str,
                        nargs='+', default=None,
                        help="partial pressure of system. "
                             "e.g. -pp O2 1e+5 N2 20000 "
                             "-> O2: 1e+5(Pa), N2: 20000(Pa)")

    parser_cpd.add_argument("-t", "--temperature",
                        dest="temperature", type=float,
                        default=0,
                        help="temperature of system (unit: K)"
                             "e.g. -t 3000 -> 3000(K)")

    # drawing diagram
    parser_cpd.add_argument("-w", "--without_label",
                        help="Draw diagram without label.",
                        action="store_true")

    parser_cpd.add_argument("-c", "--remarked_compound",
                        dest="remarked_compound", type=str,
                        default=None,
                        help="Name of compound you are remarking."
                             "Outputted equilibrium_points are "
                             "limited to neighboring that "
                             "compounds, and those equilibrium "
                             "points are labeled in "
                             "chem_pot_diagram.")

    parser_cpd.add_argument("-d", "--draw_range",
                        dest="draw_range", type=float,
                        default=None,
                        help="Drawing range of diagram."
                             "If range is shallower than the "
                             "deepest vertex, "
                             "ValueError will occur")

    # output
    parser_cpd.add_argument("-s", "--save_file",
                        dest="save_file", type=str,
                        default=None,
                        help="File name to save the drawn diagram.")

    parser_cpd.add_argument("-y", "--yaml",
                        action="store_const", const=True,
                        default=False,
                        help="Dumps yaml of remarked_compound")

    parser_cpd.set_defaults(func=chempotdiag)

    # -- plot_band -----------------------------------------------------------
    parser_plot_band = subparsers.add_parser(
        name="plot_band",
        parents=[prec_parser],
        description="Tools for plotting band structures",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        aliases=['pb'])

    parser_plot_band.add_argument(
        "-v", dest="vasprun", default="vasprun.xml", type=str)
    parser_plot_band.add_argument(
        "-v2", dest="vasprun2", type=str)
    parser_plot_band.add_argument(
        "-k", dest="kpoints", default="KPOINTS", type=str)
    parser_plot_band.add_argument(
        "-y", dest="y_range", nargs="+", type=float,
        help="Energy range, requiring two values.")
    parser_plot_band.add_argument(
        "-f", dest="filename", type=str, default=None, help="pdf file name.")
    parser_plot_band.add_argument(
        "-a", dest="absolute", action="store_true",
        help="Show in the absolute energy scale.")
    parser_plot_band.add_argument(
        "-l", dest="legend", action="store_false",
        help="Not show the legend.")

    parser_plot_band.set_defaults(func=plot_band)

    # -- plot_dos -----------------------------------------------------------
    parser_plot_dos = subparsers.add_parser(
        name="plot_dos",
        parents=[prec_parser],
        description="Tools for plotting density of states",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        aliases=['pd'])

    parser_plot_dos.add_argument(
        "-v", dest="vasprun", type=str, default="vasprun.xml")
    parser_plot_dos.add_argument(
        "-cv", dest="cbm_vbm", type=float, nargs="+",
        help="Set CBM and VBM.")
    parser_plot_dos.add_argument(
        "-t", dest="pdos_type", type=str, default="element",
        help=".")
    parser_plot_dos.add_argument(
        "-s", dest="specific", type=str, nargs="+", default=None, help=".")
    parser_plot_dos.add_argument(
        "-o", dest="orbital", action="store_false",
        help="Switch off the orbital decomposition.")
    parser_plot_dos.add_argument(
        "-x", dest="x_range", nargs="+", type=float, default=None,
        help="Set energy minimum and maximum.")
    parser_plot_dos.add_argument(
        "-y", dest="ymaxs", nargs="+", type=float, default=None,
        help="Set max values of y ranges. Support two ways."
             "1st: total_max, all_the_atoms" 
             "2nd: total_max, 1st_atom, 2nd_atom, ...")
    parser_plot_dos.add_argument(
        "-f", dest="filename", type=str, default="dos.pdf",
        help="pdf file name.")
    parser_plot_dos.add_argument(
        "-a", dest="absolute", action="store_true",
        help="Show in the absolute energy scale.")
    parser_plot_dos.add_argument(
        "-l", dest="legend", action="store_false",
        help="Not show the legend.")
    parser_plot_dos.add_argument(
        "-c", action="store_false", help="Not crop the first value.")

    parser_plot_dos.set_defaults(func=plot_dos)

    # -- band_gap --------------------------------------------------------------
    parser_band_gap = subparsers.add_parser(
        name="band_gap",
        description="Calculate the band gap from vasprun.xml",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        aliases=['bg'])

    parser_band_gap.add_argument(
        "-v", dest="vasprun", type=str, default="vasprun.xml", metavar="FILE")
    parser_band_gap.add_argument(
        "-o", dest="outcar", type=str, default="OUTCAR", metavar="FILE")
    parser_band_gap.set_defaults(func=band_gap)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

