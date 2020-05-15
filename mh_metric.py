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

# This is a code metric tool. It will implement popular and common
# metrics like path count or cyclomatic complexity.

import os
import sys
import html
import functools

import command_line
import work_package
import config

from errors import Error, ICE, Message_Handler
from m_ast import *
from m_lexer import MATLAB_Lexer
from m_parser import MATLAB_Parser

MEASURE = {m : None for m in config.METRICS}


def measures(metric_name):
    assert isinstance(metric_name, str)
    assert metric_name in config.METRICS

    def decorator(func):
        MEASURE[metric_name] = func
        return func

    return decorator


##############################################################################
# Metrics
##############################################################################

@measures("npath")
def npath(node):
    assert isinstance(node, (Sequence_Of_Statements,
                             Statement,
                             Pragma,
                             Function_Definition,
                             Script_File))

    if isinstance(node, Function_Definition):
        return npath(node.n_body)

    elif isinstance(node, Script_File):
        return npath(node.n_statements)

    elif isinstance(node, Sequence_Of_Statements):
        paths = 1
        for n_statement in node.l_statements:
            if isinstance(n_statement, (If_Statement,
                                        For_Loop_Statement,
                                        Switch_Statement,
                                        Try_Statement,
                                        While_Statement)):
                paths *= npath(n_statement)
        return paths

    elif isinstance(node, If_Statement):
        paths = 0
        for n_action in node.l_actions:
            paths += npath(n_action.n_body)
        if not node.has_else:
            paths += 1

        return paths

    elif isinstance(node, Switch_Statement):
        paths = 0
        for n_action in node.l_actions:
            paths += npath(n_action.n_body)
        if not node.has_otherwise:
            paths += 1

        return paths

    elif isinstance(node, (For_Loop_Statement, While_Statement)):
        return 1 + npath(node.n_body)

    elif isinstance(node, Try_Statement):
        return npath(node.n_body) * 2

    else:
        raise ICE("unexpected node %s" % node.__class__.__name__)


@measures("cnest")
def cnest(node):
    assert isinstance(node, (Sequence_Of_Statements,
                             Statement,
                             Pragma,
                             Function_Definition,
                             Script_File))

    if isinstance(node, Function_Definition):
        return cnest(node.n_body)

    elif isinstance(node, Script_File):
        return cnest(node.n_statements)

    elif isinstance(node, Sequence_Of_Statements):
        return max(map(cnest, node.l_statements),
                   default=0)

    elif isinstance(node, (Simple_Statement,
                           Pragma)):
        return 0

    elif isinstance(node, SPMD_Statement):
        return cnest(node.n_body)

    elif isinstance(node, If_Statement):
        return 1 + max((cnest(a.n_body) for a in node.l_actions),
                       default=0)

    elif isinstance(node, Switch_Statement):
        return 1 + max((cnest(a.n_body) for a in node.l_actions),
                       default=0)

    elif isinstance(node, (For_Loop_Statement, While_Statement)):
        return 1 + cnest(node.n_body)

    elif isinstance(node, Try_Statement):
        if node.n_handler:
            return 1 + max(cnest(node.n_body),
                           cnest(node.n_handler))
        else:
            return 1 + cnest(node.n_body)

    else:
        raise ICE("unexpected node %s" % node.__class__.__name__)


@measures("parameters")
def parameters(node):
    assert isinstance(node, (Function_Definition,
                             Script_File))

    if isinstance(node, Function_Definition):
        return len(node.n_sig.l_inputs) + len(node.n_sig.l_outputs)

    else:
        return 0


@measures("globals")
def direct_globals(node):
    assert isinstance(node, (Function_Definition,
                             Script_File))

    class Global_Visitor(AST_Visitor):
        def __init__(self):
            self.names = set()

        def visit(self, node, n_parent, relation):
            if isinstance(node, Global_Statement):
                self.names |= set(n_ident.t_ident.value
                                  for n_ident in node.l_names)

    gvis = Global_Visitor()

    if isinstance(node, Function_Definition):
        node.n_body.visit(None, gvis, "Root")
    else:
        node.n_statements.visit(None, gvis, "Root")

    return len(gvis.names)


@measures("persistent")
def persistent_variables(node):
    assert isinstance(node, (Function_Definition,
                             Script_File))

    class Persistent_Visitor(AST_Visitor):
        def __init__(self):
            self.names = set()

        def visit(self, node, n_parent, relation):
            if isinstance(node, Persistent_Statement):
                self.names |= set(n_ident.t_ident.value
                                  for n_ident in node.l_names)

    pvis = Persistent_Visitor()

    if isinstance(node, Function_Definition):
        node.n_body.visit(None, pvis, "Root")
    else:
        node.n_statements.visit(None, pvis, "Root")

    return len(pvis.names)


@measures("function_length")
def function_length(node):
    assert isinstance(node, (Function_Definition,
                             Script_File))

    if isinstance(node, Script_File):
        return None

    elif node.t_end:
        return node.t_end.location.line - node.t_fun.location.line + 1

    else:
        # If a function does not have an end, its length is from this
        # function up to and including the next function or the end of
        # the file
        n_cu = node.n_parent
        if not isinstance(n_cu, Function_File):
            raise ICE("unterminated function must be a child of a "
                      "function file")

        this_function_idx = n_cu.l_functions.index(node)
        next_function_idx = this_function_idx + 1

        if next_function_idx < len(n_cu.l_functions):
            # We have a function following this one
            return (n_cu.l_functions[next_function_idx].t_fun.location.line -
                    node.t_fun.location.line)

        else:
            # This is the last function in the file
            return n_cu.file_length - node.t_fun.location.line + 1


@measures("cyc")
def cyclomatic_complexity(node):
    assert isinstance(node, (Function_Definition,
                             Script_File))
    # See
    # https://uk.mathworks.com/help/matlab/ref/logicaloperatorsshortcircuit.html
    # for short-circuit semantics

    class Cyclomatic_Complexity_Visitor(AST_Visitor):
        def __init__(self):
            self.metric = 1

        def visit(self, node, n_parent, relation):
            if isinstance(node, Binary_Logical_Operation):
                if node.short_circuit:
                    self.metric += 1
            elif isinstance(node, (For_Loop_Statement,
                                   While_Statement,
                                   Try_Statement)):
                self.metric += 1
            elif isinstance(node, If_Statement):
                if node.has_else:
                    self.metric += len(node.l_actions) - 1
                else:
                    self.metric += len(node.l_actions)
            elif isinstance(node, Switch_Statement):
                if node.has_otherwise:
                    self.metric += len(node.l_actions) - 1
                else:
                    self.metric += len(node.l_actions)

    cvis = Cyclomatic_Complexity_Visitor()

    if isinstance(node, Function_Definition):
        node.n_body.visit(None, cvis, "Root")
    else:
        node.n_statements.visit(None, cvis, "Root")

    return cvis.metric


##############################################################################
# Infrastructure
##############################################################################

def check_metric(mh, cfg, loc, metric, metrics, justifications):
    if config.metric_check(cfg, metric):
        measure = metrics[metric]["measure"]

        if measure is None:
            return

        limit = config.metric_upper_limit(cfg, metric)
        metrics[metric]["limit"] = limit
        if measure > limit:
            if metric in justifications:
                mh.metric_justifications += 1
                justifications[metric].applies = True
                metrics[metric]["reason"] = justifications[metric].reason()
            else:
                mh.metric_issue(loc,
                                "exceeded %s: measured %u > limit %u" %
                                (metric, measure, limit))


def get_justifications(mh, n_root):
    assert isinstance(mh, Message_Handler)
    assert isinstance(n_root, Sequence_Of_Statements)

    justifications = {}

    for n_statement in n_root.l_statements:
        if isinstance(n_statement, Metric_Justification_Pragma):
            if n_statement.metric() in justifications:
                mh.warning(n_statement.loc(),
                           "duplicate justification for %s" %
                           n_statement.metric)
            else:
                justifications[n_statement.metric()] = n_statement

    return justifications


def get_file_justifications(mh, n_cu):
    assert isinstance(mh, Message_Handler)
    assert isinstance(n_cu, Compilation_Unit)

    justifications = {}

    if isinstance(n_cu, Script_File):
        # Pragmas are in the top statement list
        for n_statement in n_cu.n_statements.l_statements:
            if isinstance(n_statement, Metric_Justification_Pragma):
                if n_statement.metric() in justifications:
                    mh.warning(n_statement.loc(),
                               "duplicate justification for %s" %
                               n_statement.metric())
                else:
                    justifications[n_statement.metric()] = n_statement

    else:
        # Pragmas are in the dedicated file pragma list
        for n_pragma in n_cu.l_pragmas:
            if isinstance(n_pragma, Metric_Justification_Pragma):
                if n_pragma.metric() in justifications:
                    mh.warning(n_pragma.loc(),
                               "duplicate justification for %s" %
                               n_pragma.metric())
                else:
                    justifications[n_pragma.metric()] = n_pragma

    return justifications


def get_function_metrics(mh, cfg, tree):
    assert isinstance(tree, Compilation_Unit)

    metrics = {}
    justifications = {}

    def process_function(n_fdef, naming_stack):
        assert isinstance(n_fdef, Function_Definition)

        # We need a unique name for the function for this function.
        name = "::".join(map(str, naming_stack + [n_fdef.n_sig.n_name]))

        metrics[name] = {m: {"measure" : MEASURE[m](n_fdef),
                             "limit"   : None,
                             "reason"  : None}
                         for m in config.FUNCTION_METRICS}

        justifications[name] = get_justifications(mh, n_fdef.n_body)

        return name

    def process_script(n_script):
        assert isinstance(n_script, Script_File)

        # We need a unique name for the script
        name = n_script.name.rsplit(".")[0]

        metrics[name] = {m: {"measure" : MEASURE[m](n_script),
                             "limit"   : None,
                             "reason"  : None}
                         for m in config.FUNCTION_METRICS}

        justifications[name] = get_justifications(mh, n_script.n_statements)

        return name

    class Function_Visitor(AST_Visitor):
        def __init__(self):
            self.name_stack = []

        def visit(self, node, n_parent, relation):
            name = None
            if isinstance(node, Function_Definition):
                name = process_function(node, self.name_stack)
                self.name_stack.append(node.n_sig.n_name)
            elif isinstance(node, Class_Definition):
                self.name_stack.append(node.n_name)
            elif isinstance(node, Script_File):
                name = process_script(node)
                self.name_stack.append(node.name)

            # Check+justify function metrics

            if name is not None:
                for function_metric in config.FUNCTION_METRICS:
                    check_metric(mh, cfg, node.loc(), function_metric,
                                 metrics[name],
                                 justifications[name])

        def visit_end(self, node, n_parent, relation):
            if isinstance(node, Definition):
                self.name_stack.pop()

    tree.visit(None, Function_Visitor(), "Root")
    return metrics


def warn_unused_justifications(mh, n_cu):
    assert isinstance(mh, Message_Handler)
    assert isinstance(n_cu, Compilation_Unit)

    class Justification_Visitor(AST_Visitor):
        def __init__(self):
            self.name_stack = []

        def visit(self, node, n_parent, relation):
            if isinstance(node, Metric_Justification_Pragma):
                if not node.applies:
                    mh.warning(node.loc(),
                               "this justification does not apply to anything")

    n_cu.visit(None, Justification_Visitor(), "Root")


def collect_metrics(mh, cfg, content, filename, blockname):
    if blockname is None:
        full_name = filename
    else:
        full_name = filename + "/" + blockname

    metrics = {full_name: {"errors"    : False,
                           "metrics"   : {},
                           "functions" : {}}}

    # Create lexer

    lexer = MATLAB_Lexer(mh, content, filename, blockname)
    if cfg["octave"]:
        lexer.set_octave_mode()
    if cfg["ignore_pragmas"]:
        lexer.process_pragmas = False

    # We're dealing with an empty file here. Lets just not do anything

    if len(lexer.text.strip()) == 0:
        return metrics

    # Create parse tree

    try:
        parser = MATLAB_Parser(mh, lexer, cfg)
        parse_tree = parser.parse_file()
    except Error:
        metrics[filename]["errors"] = True
        return metrics

    # File metrics

    metrics[full_name]["metrics"] = {
        "file_length" : {"measure" : lexer.line_count(),
                         "limit"   : None,
                         "reason"  : None}
    }
    justifications = {full_name : get_file_justifications(mh, parse_tree)}

    # Check+justify file metrics

    for file_metric in config.FILE_METRICS:
        check_metric(mh, cfg, lexer.get_file_loc(), file_metric,
                     metrics[full_name]["metrics"],
                     justifications[full_name])

    # Collect, check, and justify function metrics

    metrics[full_name]["functions"] = get_function_metrics(mh, cfg, parse_tree)

    # Complain about unused justifications

    warn_unused_justifications(mh, parse_tree)

    # Return results

    return metrics


def write_text_report(fd, all_metrics, worst_offenders):
    first = True

    fd.write("=== Code metric by file:\n\n")
    for filename in sorted(all_metrics):
        metrics = all_metrics[filename]
        if first:
            first = False
        else:
            fd.write("\n")
        fd.write("* Code metrics for file %s:\n" % filename)

        if metrics["errors"]:
            fd.write("  Contains syntax or semantics errors,\n")
            fd.write("  no metrics collected.\n")
            continue

        for file_metric in config.FILE_METRICS:
            results = metrics["metrics"][file_metric]
            if results["measure"] is None:
                continue
            fd.write("  %s: %u" % (file_metric, results["measure"]))
            if results["reason"]:
                fd.write(" (%s)\n" % results["reason"])
            elif results["limit"] and results["measure"] > results["limit"]:
                fd.write(" (!not justified!)\n")
            else:
                fd.write("\n")

        for function in sorted(metrics["functions"]):
            fd.write("\n")
            fd.write("  Code metrics for function %s:\n" % function)
            for function_metric in config.FUNCTION_METRICS:
                results = metrics["functions"][function][function_metric]
                if results["measure"] is None:
                    continue
                fd.write("    %s: %u" % (function_metric, results["measure"]))
                if results["reason"]:
                    fd.write(" (%s)\n" % results["reason"])
                elif results["limit"] and \
                     results["measure"] > results["limit"]:
                    fd.write(" (!not justified!)\n")
                else:
                    fd.write("\n")

    if worst_offenders:
        fd.write("\n=== Global summary of worst offenders by metric:\n\n")

        for file_metric in config.FILE_METRICS:
            fd.write("* File metric %s:\n" % file_metric)
            for rank, file_name in enumerate(worst_offenders[file_metric], 1):
                if file_name:
                    mdata = all_metrics[file_name]["metrics"][file_metric]
                    fd.write("  %u. %u (%s)\n" % (rank,
                                                  mdata["measure"],
                                                  file_name))
            fd.write("\n")

        for function_metric in config.FUNCTION_METRICS:
            fd.write("* Function metric %s:\n" % function_metric)
            for rank, tup in enumerate(worst_offenders[function_metric], 1):
                if tup:
                    file_name, function_name = tup
                    mdata = (all_metrics[file_name]["functions"]
                             [function_name][function_metric])
                    fd.write("  %u. %u (%s, function %s)\n" %
                             (rank,
                              mdata["measure"],
                              file_name,
                              function_name))
            fd.write("\n")


def write_html_report(fd, fd_name, all_metrics, worst_offenders):
    docs_dir = os.path.dirname(os.path.relpath(
        os.path.join(sys.path[0], "docs", "style.css"),
        os.path.dirname(os.path.abspath(fd_name)))).replace("\\", "/")

    fd.write("<!DOCTYPE html>\n")
    fd.write("<html>\n")
    fd.write("<head>\n")
    fd.write("<meta charset=\"UTF-8\">\n")
    # Link style-sheet with a relative path based on where the
    # output report file will be
    fd.write("<link rel=\"stylesheet\" href=\"file:%s/style.css\">\n" %
             docs_dir)
    fd.write("<title>MISS_HIT Report</title>\n")
    fd.write("</head>\n")
    fd.write("<body>\n")
    fd.write("<header>MISS_HIT Report</header>\n")
    fd.write("<main>\n")
    fd.write("<div></div>\n")

    # Produce worst-offender table
    if worst_offenders:
        fd.write("<div class='title'>\n")
        fd.write("<img src='%s/assets/alert-triangle.svg' alt='Warning'>\n" %
                 docs_dir)
        fd.write("<h1>Worst offenders</h1>\n")
        fd.write("</div>\n")
        fd.write("<section>\n")

        fd.write("<div class='metrics'>\n")
        fd.write("<table>\n")

        fd.write("<thead>\n")
        fd.write("<tr>\n")
        fd.write("  <td>Rank</td>\n")
        for file_metric in config.FILE_METRICS:
            fd.write("  <td>%s</td>\n" % file_metric)
        for function_metric in config.FUNCTION_METRICS:
            fd.write("  <td>%s</td>\n" % function_metric)
        fd.write("</tr>\n")
        fd.write("</thead>\n")
        fd.write("<tbody>\n")

        count = worst_offender_count(worst_offenders)
        for rank in range(count):
            fd.write("<tr>\n")
            fd.write("  <td>%s</td>\n" % (rank + 1))
            for file_metric in config.FILE_METRICS:
                file_name = worst_offenders[file_metric][rank]
                if file_name:
                    mdata = all_metrics[file_name]["metrics"][file_metric]
                    fd.write("  <td class='tip' tip='%s'>"
                             "<a href='#%s'>%u</a></td>\n" %
                             (os.path.basename(file_name),
                              file_name,
                              mdata["measure"]))

                else:
                    fd.write("  <td class='na'></td>\n")
            for function_metric in config.FUNCTION_METRICS:
                metric = worst_offenders[function_metric][rank]
                if metric:
                    file_name, function_name = metric
                    mdata = (all_metrics[file_name]["functions"]
                             [function_name][function_metric])
                    fd.write("  <td class='tip' tip='%s'>"
                             "<a href='#%s'>%u</a></td>\n" %
                             ("%s in file %s" % (function_name,
                                                 os.path.basename(file_name)),
                              file_name,

                              mdata["measure"]))

                else:
                    fd.write("  <td class='na'></td>\n")

            fd.write("</tr>\n")

        fd.write("</tbody>\n")
        fd.write("</table>\n")
        fd.write("</div>\n")

        fd.write("</section>\n")

    # Produce full list of metrics
    fd.write("<div class='title'>\n")
    fd.write("<img src='%s/assets/bar-chart-2.svg' alt='Warning'>\n" %
             docs_dir)
    fd.write("<h1>Code metrics by file</h1>\n")
    fd.write("</div>\n")
    fd.write("<section>\n")

    for filename in sorted(all_metrics):
        metrics = all_metrics[filename]

        fd.write("<div class='metrics'>\n")
        fd.write("<h2><a name='%s'>%s</a></h2>\n" % (filename,
                                                     filename))
        fd.write("<table>\n")

        fd.write("<thead>\n")
        fd.write("<tr>\n")
        fd.write("  <td>Item</td>\n")
        for file_metric in config.FILE_METRICS:
            fd.write("  <td>%s</td>\n" % file_metric)
        for function_metric in config.FUNCTION_METRICS:
            fd.write("  <td>%s</td>\n" % function_metric)
        fd.write("</tr>\n")
        fd.write("</thead>\n")
        fd.write("<tbody>\n")

        fd.write("<tr>\n")
        fd.write("  <td>%s</td>\n" % os.path.basename(filename))
        for file_metric in config.FILE_METRICS:
            results = metrics["metrics"][file_metric]
            if results["measure"] is None:
                fd.write("  <td class='na'></td>\n")
            elif results["reason"]:
                fd.write("  <td class='ok_justified tip' tip='%s'>%u</td>\n" %
                         ("Justification: " + html.escape(results["reason"]),
                          results["measure"]))
            elif results["limit"] and results["measure"] > results["limit"]:
                fd.write("  <td class='nok'>%u</td>\n" %
                         results["measure"])
            else:
                fd.write("<td class='ok'>%u</td>" % results["measure"])
        fd.write("  <td class='na'></td>\n" * len(config.FUNCTION_METRICS))
        fd.write("</tr>\n")

        for function in sorted(metrics["functions"]):
            fd.write("<tr>\n")
            fd.write("  <td><a name='%s'></a>%s</td>\n" % (function,
                                                           function))
            fd.write("  <td class='na'></td>\n" * len(config.FILE_METRICS))
            for function_metric in config.FUNCTION_METRICS:
                results = metrics["functions"][function][function_metric]
                if results["measure"] is None:
                    fd.write("  <td class='na'></td>\n")
                elif results["reason"]:
                    fd.write("  <td class='ok_justified tip' tip='%s'>"
                             "%u</td>\n" %
                             ("Justification: " +
                              html.escape(results["reason"]),
                              results["measure"]))
                elif results["limit"] and \
                     results["measure"] > results["limit"]:
                    fd.write("  <td class='nok'>%u</td>\n" %
                             results["measure"])
                else:
                    fd.write("  <td class='ok'>%u</td>\n" % results["measure"])
            fd.write("</tr>\n")

        fd.write("</tbody>\n")
        fd.write("</table>\n")
        fd.write("</div>\n")

    fd.write("</section>\n")
    fd.write("</main>\n")
    fd.write("<footer>\n")
    fd.write("MISS_HIT is licensed under the GPLv3\n")
    fd.write("</footer>\n")
    fd.write("</body>\n")
    fd.write("</html>\n")


def worst_offender_count(worst_offenders):
    for metric in worst_offenders:
        return len(worst_offenders[metric])
    raise ICE("cannot determine length of wo table")


def build_worst_offenders_table(all_metrics, count):
    assert isinstance(count, int) and count >= 1
    # all_metrics = {filename -> {errors : bool
    #                             functions : {fn_name -> MD}
    #                             metrics : MD}}
    # MD = {m_name -> {measure : INT
    #                  limit   : INT
    #                  reason  : STR}}

    wot = {}
    # file_metric -> [ filename, ... ]
    # fn_metric -> [ (filename, fn_name), ... ]

    def key_file_metric(file_name, metric):
        return (all_metrics[file_name]["metrics"][metric]["measure"],
                file_name)

    def key_function_metric(name_tuple, metric):
        file_name, function_name = name_tuple
        metrics = all_metrics[file_name]["functions"][function_name]
        return (metrics[metric]["measure"],
                file_name,
                function_name)

    for file_metric in config.FILE_METRICS:
        wot[file_metric] = []
        for file_name in all_metrics:
            metrics = all_metrics[file_name]
            if metrics["errors"]:
                continue
            if not metrics["metrics"][file_metric]["measure"]:
                continue
            wot[file_metric].append(file_name)
        key_fn = functools.partial(key_file_metric,
                                   metric=file_metric)
        wot[file_metric].sort(key=key_fn, reverse=True)

    for function_metric in config.FUNCTION_METRICS:
        wot[function_metric] = []
        for file_name in all_metrics:
            metrics = all_metrics[file_name]
            if metrics["errors"]:
                continue
            for function_name in metrics["functions"]:
                function_metrics = metrics["functions"][function_name]
                if not function_metrics[function_metric]["measure"]:
                    continue
                wot[function_metric].append((file_name, function_name))
        key_fn = functools.partial(key_function_metric,
                                   metric=function_metric)
        wot[function_metric].sort(key=key_fn, reverse=True)

    # Make sure the length is as expected for each metric
    for metric in config.METRICS:
        if len(wot[metric]) < count:
            wot[metric] += [None] * (count - len(wot[metric]))
        elif len(wot[metric]) > count:
            wot[metric] = wot[metric][:count]
        assert len(wot[metric]) == count

    return wot


class MH_Metric_Result(work_package.Result):
    def __init__(self, wp, metrics):
        super().__init__(wp, True)
        self.metrics = metrics


class MH_Metric(command_line.MISS_HIT_Back_End):
    def __init__(self, options):
        super().__init__("MH Metric")

        self.options = options

        self.metrics = {}
        # file -> { metrics -> {}
        #           functions -> {name -> {}} }

    @classmethod
    def process_wp(cls, wp):
        if wp.blockname is None:
            full_name = wp.filename
        else:
            full_name = wp.filename + "/" + wp.blockname

        metrics = {full_name: {"errors"    : False,
                               "metrics"   : {},
                               "functions" : {}}}

        # Create lexer

        lexer = MATLAB_Lexer(wp.mh, wp.get_content(),
                             wp.filename, wp.blockname)
        if wp.cfg["octave"]:
            lexer.set_octave_mode()
        if wp.cfg["ignore_pragmas"]:
            lexer.process_pragmas = False

        # We're dealing with an empty file here. Lets just not do anything

        if len(lexer.text.strip()) == 0:
            return MH_Metric_Result(wp, metrics)

        # Create parse tree

        try:
            parser = MATLAB_Parser(wp.mh, lexer, wp.cfg)
            parse_tree = parser.parse_file()
        except Error:
            metrics[wp.filename]["errors"] = True
            return MH_Metric_Result(wp, metrics)

        # File metrics

        metrics[full_name]["metrics"] = {
            "file_length" : {"measure" : lexer.line_count(),
                             "limit"   : None,
                             "reason"  : None}
        }
        justifications = {full_name : get_file_justifications(wp.mh,
                                                              parse_tree)}

        # Check+justify file metrics

        for file_metric in config.FILE_METRICS:
            check_metric(wp.mh, wp.cfg, lexer.get_file_loc(), file_metric,
                         metrics[full_name]["metrics"],
                         justifications[full_name])

        # Collect, check, and justify function metrics

        metrics[full_name]["functions"] = get_function_metrics(wp.mh,
                                                               wp.cfg,
                                                               parse_tree)

        # Complain about unused justifications

        warn_unused_justifications(wp.mh, parse_tree)

        return MH_Metric_Result(wp, metrics)

    def process_result(self, result):
        assert isinstance(result, work_package.Result)

        if isinstance(result, MH_Metric_Result):
            assert result.processed
            self.metrics.update(result.metrics)

        else:
            assert not result.processed

    def post_process(self):
        # Build worst offenders table, if requested

        if self.options.worst_offenders:
            worst_offenders = build_worst_offenders_table(
                self.metrics,
                self.options.worst_offenders)
        else:
            worst_offenders = None

        # Generate report

        if self.options.html:
            with open(self.options.html, "w") as fd:
                write_html_report(fd,
                                  self.options.html,
                                  self.metrics,
                                  worst_offenders)
        elif self.options.text:
            with open(self.options.text, "w") as fd:
                write_text_report(fd, self.metrics, worst_offenders)
        elif not self.options.ci:
            write_text_report(sys.stdout, self.metrics, worst_offenders)


def main():
    clp = command_line.create_basic_clp()

    clp["output_options"].add_argument(
        "--worst-offenders",
        default=10,
        type=int,
        help=("Produce a table of the worst offenders for each metric."
              " By default this is 10; setting it to 0 disables this"
              " feature."))

    clp["output_options"].add_argument(
        "--ci",
        default=False,
        action="store_true",
        help=("Do not print any metrics report, only notify about violations."
              "This is the intended way to run in a CI environment."))

    clp["output_options"].add_argument(
        "--text",
        default=None,
        metavar="FILE",
        help=("Print plain-text metrics summary to the given file. By"
              " default we print the summary to standard output."))

    clp["output_options"].add_argument(
        "--html",
        default=None,
        metavar="FILE",
        help=("Write HTML metrics report to the file."))

    options = command_line.parse_args(clp)

    if options.text:
        if os.path.exists(options.text) and not os.path.isfile(options.text):
            clp["ap"].error("cannot write metrics to %s, it exists and is"
                            " not a file" % options.text)

    if options.html:
        if os.path.exists(options.html) and not os.path.isfile(options.html):
            clp["ap"].error("cannot write metrics to %s, it exists and is"
                            " not a file" % options.text)

    if options.text and options.html:
        clp["ap"].error("the text and html options are mutually exclusive")

    if options.ci and (options.text or options.html):
        clp["ap"].error("the CI mode and and text/html options are mutually "
                        "exclusive")

    if options.worst_offenders < 0:
        clp["ap"].error("the worst-offender option cannot be negative")

    mh = Message_Handler("metric")
    mh.show_context = not options.brief
    mh.show_style   = False
    mh.autofix      = False

    metric_backend = MH_Metric(options)
    command_line.execute(mh, options, {}, metric_backend)


if __name__ == "__main__":
    command_line.ice_handler(main)
