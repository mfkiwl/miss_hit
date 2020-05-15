#!/usr/bin/env python3
##############################################################################
##                                                                          ##
##          MATLAB Independent, Small & Safe, High Integrity Tools          ##
##                                                                          ##
##              Copyright (C) 2020, Florian Schanda                         ##
##                                                                          ##
##  This file is part of MISS_HIT.                                          ##
##                                                                          ##
##  MATLAB Independent, Small & Safe, High Integrity Tools (MISS_HIT) is    ##
##  free software: you can redistribute it and/or modify it under the       ##
##  terms of the GNU General Public License as published by the Free        ##
##  Software Foundation, either version 3 of the License, or (at your       ##
##  option) any later version.                                              ##
##                                                                          ##
##  MISS_HIT is distributed in the hope that it will be useful,             ##
##  but WITHOUT ANY WARRANTY; without even the implied warranty of          ##
##  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           ##
##  GNU General Public License for more details.                            ##
##                                                                          ##
##  You should have received a copy of the GNU General Public License       ##
##  along with MISS_HIT. If not, see <http://www.gnu.org/licenses/>.        ##
##                                                                          ##
##############################################################################

# This is the common command-line handling for all MISS_HIT tools.

import os
import sys
import argparse
import traceback
import textwrap
import multiprocessing
import functools

import config_files
import errors
import work_package
import s_parser
from version import GITHUB_ISSUES, VERSION, FULL_NAME


def create_basic_clp():
    rv = {}

    ap = argparse.ArgumentParser(
        description="MATLAB Independent, Small & Safe, High Integrity Tools")
    rv["ap"] = ap

    ap.add_argument("-v", "--version",
                    action="store_true",
                    default=False,
                    help="Show version and exit")
    ap.add_argument("files",
                    metavar="FILE|DIR",
                    nargs="*",
                    help="MATLAB/SIMULINK files or directories to analyze")
    ap.add_argument("--single",
                    action="store_true",
                    default=False,
                    help="Do not use multi-threaded analysis")
    ap.add_argument("--ignore-config",
                    action="store_true",
                    default=False,
                    help=("Ignore all %s files." %
                          " or ".join(config_files.CONFIG_FILENAMES)))

    output_options = ap.add_argument_group("output options")
    rv["output_options"] = output_options

    output_options.add_argument("--brief",
                                action="store_true",
                                default=False,
                                help="Don't show line-context on messages")

    language_options = ap.add_argument_group("language options")
    rv["language_options"] = language_options

    language_options.add_argument("--octave",
                                  default=False,
                                  action="store_true",
                                  help=("Enable support for the Octave"
                                        " language. Note: This is highly"
                                        " incomplete right now, only the"
                                        " # comments are supported."))

    language_options.add_argument("--ignore-pragmas",
                                  default=False,
                                  action="store_true",
                                  help=("Disable special treatment of"
                                        " MISS_HIT pragmas. These are"
                                        " comments that start with '%% mh:'"))

    debug_options = ap.add_argument_group("debugging options")
    rv["debug_options"] = debug_options

    return rv


def parse_args(clp):
    options = clp["ap"].parse_args()

    if options.version:
        print(FULL_NAME)
        sys.exit(0)

    # False alarm from pylint
    # pylint: disable=no-member
    if not options.brief and sys.stdout.encoding.lower() != "utf-8":
        print("WARNING: It looks like your environment is not set up quite")
        print("         right since python will encode to %s on stdout." %
              sys.stdout.encoding)
        print()
        print("To fix set one of the following environment variables:")
        print("   LC_ALL=en_GB.UTF-8 (or something similar)")
        print("   PYTHONIOENCODING=UTF-8")

    if not options.files:
        clp["ap"].error("at least one file or directory is required")

    for item in options.files:
        if not (os.path.isdir(item) or os.path.isfile(item)):
            clp["ap"].error("%s is neither a file nor directory" % item)

    return options


class MISS_HIT_Back_End:
    def __init__(self, name):
        assert isinstance(name, str)
        self.name = name

    @classmethod
    def process_wp(cls, wp):
        raise errors.ICE("unimplemented process_wp function")

    def process_result(self, result):
        pass

    def post_process(self):
        pass


def dispatch_wp(process_fn, wp):
    results = []

    try:
        if not wp.cfg["enable"]:
            wp.mh.register_exclusion(wp.filename)
            results.append(work_package.Result(wp, False))

        elif isinstance(wp, work_package.SIMULINK_File_WP):
            wp.register_file()
            smdl = s_parser.SIMULINK_Model(wp.mh, wp.filename)
            for block in smdl.matlab_blocks:
                block_wp = work_package.Embedded_MATLAB_WP(wp, block)
                results.append(process_fn(block_wp))

        elif isinstance(wp, work_package.MATLAB_File_WP):
            wp.register_file()
            results.append(process_fn(wp))

        else:
            raise errors.ICE("unknown work package kind %s" %
                             wp.__class__.__name__)

    except errors.Error:
        raise errors.ICE("uncaught Error in process_wp")

    return results


def execute(mh, options, extra_options, back_end, process_slx=True):
    assert isinstance(mh, errors.Message_Handler)
    assert isinstance(back_end, MISS_HIT_Back_End)

    process_fn = functools.partial(dispatch_wp, back_end.process_wp)

    try:
        for item in options.files:
            if os.path.isdir(item):
                config_files.register_tree(mh,
                                           os.path.abspath(item),
                                           options)

            elif os.path.isfile(item):
                config_files.register_tree(
                    mh,
                    os.path.dirname(os.path.abspath(item)),
                    options)

        config_files.build_config_tree(mh,
                                       options)

    except errors.Error:
        mh.summary_and_exit()

    work_list = []
    for item in options.files:
        if os.path.isdir(item):
            for path, dirs, files in os.walk(item):
                if path == ".":
                    path = ""
                dirs.sort()
                for f in sorted(files):
                    if f.endswith(".m") or (f.endswith(".slx") and
                                            process_slx):
                        work_list.append(
                            work_package.create(os.path.join(path, f),
                                                "cp1252",
                                                mh,
                                                options, extra_options))

        elif item.endswith(".m") or (item.endswith(".slx") and process_slx):
            work_list.append(work_package.create(item,
                                                 "cp1252",
                                                 mh,
                                                 options, extra_options))

        else:
            pass

    if options.single:
        for wp in work_list:
            for result in process_fn(wp):
                assert isinstance(result, work_package.Result)
                mh.integrate(result.wp.mh)
                if result.processed:
                    mh.finalize_file(result.wp.filename)
                    back_end.process_result(result)

    else:
        pool = multiprocessing.Pool()
        for results in pool.imap(process_fn,
                                 work_list,
                                 5):
            for result in results:
                assert isinstance(result, work_package.Result)
                mh.integrate(result.wp.mh)
                if result.processed:
                    mh.finalize_file(result.wp.filename)
                    back_end.process_result(result)

    back_end.post_process()

    mh.summary_and_exit()


def ice_handler(main_function):
    try:
        main_function()
    except errors.ICE as internal_compiler_error:
        traceback.print_exc()
        print("-" * 70)
        print("- Encountered an internal compiler error. This is a tool")
        print("- bug, please report it on our github issues so we can fix it:")
        print("-")
        print("-    %s" % GITHUB_ISSUES)
        print("-")
        print("- Please include the above backtrace in your bug report, and")
        print("- the following information:")
        print("-")
        print("- MISS_HIT version: %s" % VERSION)
        print("-")
        lines = textwrap.wrap(internal_compiler_error.reason)
        print("\n".join("- %s" % l for l in lines))
        print("-" * 70)
