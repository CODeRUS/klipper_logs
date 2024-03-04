import sys
import os
import random

from kconfiglib import Kconfig, \
                       Symbol, MENU, COMMENT, \
                       BOOL, TRISTATE, STRING, INT, HEX, UNKNOWN, \
                       expr_value


klipper_folder = '/home/coderus/klipper'
os.environ['srctree'] = klipper_folder


def indent_print(s, indent):
    return indent * " " + s


def value_str(sc):
    if sc.type in (STRING, INT, HEX):
        return "({})".format(sc.str_value)

    if isinstance(sc, Symbol) and sc.choice and sc.visibility == 2:
        return "-->" if sc.choice.selection is sc else "   "

    tri_val_str = (" ", "M", "*")[sc.tri_value]

    if len(sc.assignable) == 1:
        # Pinned to a single value
        return "-{}-".format(tri_val_str)

    if sc.type == BOOL:
        return "[{}]".format(tri_val_str)

    if sc.type == TRISTATE:
        if sc.assignable == (1, 2):
            return "{" + tri_val_str + "}" 
        return "<{}>".format(tri_val_str)


def node_str(node):
    if not node.prompt:
        return ""

    prompt, prompt_cond = node.prompt
    if not expr_value(prompt_cond):
        return ""

    if node.item == MENU:
        return "    " + prompt

    if node.item == COMMENT:
        return "    *** {} ***".format(prompt)

    sc = node.item

    if sc.type == UNKNOWN:
        return ""

    if isinstance(sc, Symbol) and sc.choice and sc.visibility == 2 and not sc.choice.selection is sc:
        return ""

    res = "{:3} {}".format(value_str(sc), prompt)

    if sc.name is not None:
        res += " ({})".format(sc.name)

    return res


def print_menuconfig_nodes(node, indent):
    ret = []
    while node:
        string = node_str(node)
        if string:
            ret += [indent_print(string, indent)]

        if node.list:
            ret += print_menuconfig_nodes(node.list, indent + 8)

        node = node.next

    return ret


def print_menuconfig(kconf):
    ret = ["======== {} ========\n".format(kconf.mainmenu_text)]
    ret += print_menuconfig_nodes(kconf.top_node.list, 0)
    return ret


def print_config_file(config):
    kconf = Kconfig(f'{klipper_folder}/src/Kconfig')
    kconf.load_config(config)
    ret = print_menuconfig(kconf)
    # os.remove(config)
    return ret


def print_config(config):
    fname = os.path.join('/tmp/', str(random.getrandbits(128)))
    f = open(fname, 'w')
    f.write(config)
    f.write('CONFIG_LOW_LEVEL_OPTIONS=y\n')
    f.flush()
    f.close()

    # os.remove(fname)
    return print_config_file(fname)
