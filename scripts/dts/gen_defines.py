#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# Copyright (c) 2019 Linaro Limited
# SPDX-License-Identifier: BSD-3-Clause

# This script uses edtlib to generate a header file and a .conf file (both
# containing the same values) from a device tree (.dts) file. Information from
# binding files in YAML format is used as well.
#
# Bindings are files that describe device tree nodes. Device tree nodes are
# usually mapped to bindings via their 'compatible = "..."' property.
#
# See the docstring/comments at the top of edtlib.py for more information.
#
# Note: Do not access private (_-prefixed) identifiers from edtlib here (and
# also note that edtlib is not meant to expose the dtlib API directly).
# Instead, think of what API you need, and add it as a public documented API in
# edtlib. This will keep this script simple.

import argparse
import sys

import edtlib


def main():
    global conf_file
    global header_file

    args = parse_args()

    try:
        edt = edtlib.EDT(args.dts, args.bindings_dirs)
    except edtlib.EDTError as e:
        sys.exit("device tree error: " + str(e))

    conf_file = open(args.conf_out, "w", encoding="utf-8")
    header_file = open(args.header_out, "w", encoding="utf-8")

    out_comment("Generated by gen_defines.py", blank_before=False)
    out_comment("DTS input file: " + args.dts, blank_before=False)
    out_comment("Directories with bindings: " + ", ".join(args.bindings_dirs),
                blank_before=False)

    active_compats = set()

    for dev in edt.devices:
        if dev.enabled and dev.matching_compat:
            # Skip 'fixed-partitions' devices since they are handled by
            # write_flash() and would generate extra spurious #defines
            if dev.matching_compat == "fixed-partitions":
                continue

            out_comment("Device tree node: " + dev.path)
            out_comment("Binding (compatible = {}): {}".format(
                            dev.matching_compat, dev.binding_path),
                        blank_before=False)
            out_comment("Binding description: " + dev.description,
                        blank_before=False)

            write_regs(dev)
            write_irqs(dev)
            write_gpios(dev)
            write_pwms(dev)
            write_iochannels(dev)
            write_clocks(dev)
            write_spi_dev(dev)
            write_props(dev)
            write_bus(dev)
            write_existence_flags(dev)

            active_compats.update(dev.compats)

    out_comment("Active compatibles (mentioned in DTS + binding found)")
    for compat in sorted(active_compats):
        #define DT_COMPAT_<COMPAT> 1
        out("COMPAT_{}".format(str2ident(compat)), 1)

    # These are derived from /chosen
    write_addr_size(edt, "zephyr,sram", "SRAM")
    write_addr_size(edt, "zephyr,ccm", "CCM")
    write_addr_size(edt, "zephyr,dtcm", "DTCM")

    # NOTE: These defines aren't used by the code and just used by
    # the kconfig build system, we can remove them in the future
    # if we provide a function in kconfigfunctions.py to get
    # the same info
    write_required_label("UART_CONSOLE_ON_DEV_NAME", edt.chosen_dev("zephyr,console"))
    write_required_label("UART_SHELL_ON_DEV_NAME",   edt.chosen_dev("zephyr,shell-uart"))
    write_required_label("BT_UART_ON_DEV_NAME",      edt.chosen_dev("zephyr,bt-uart"))
    write_required_label("UART_PIPE_ON_DEV_NAME",    edt.chosen_dev("zephyr,uart-pipe"))
    write_required_label("BT_MONITOR_ON_DEV_NAME",   edt.chosen_dev("zephyr,bt-mon-uart"))
    write_required_label("UART_MCUMGR_ON_DEV_NAME",  edt.chosen_dev("zephyr,uart-mcumgr"))
    write_required_label("BT_C2H_UART_ON_DEV_NAME",  edt.chosen_dev("zephyr,bt-c2h-uart"))

    write_flash(edt.chosen_dev("zephyr,flash"))
    write_code_partition(edt.chosen_dev("zephyr,code-partition"))

    flash_index = 0
    for dev in edt.devices:
        if dev.name.startswith("partition@"):
            write_flash_partition(dev, flash_index)
            flash_index += 1

    out_comment("Number of flash partitions")
    if flash_index != 0:
        out("FLASH_AREA_NUM", flash_index)

    print("Device tree configuration written to " + args.conf_out)


def parse_args():
    # Returns parsed command-line arguments

    parser = argparse.ArgumentParser()
    parser.add_argument("--dts", required=True, help="DTS file")
    parser.add_argument("--bindings-dirs", nargs='+', required=True,
                        help="directory with bindings in YAML format, "
                        "we allow multiple")
    parser.add_argument("--header-out", required=True,
                        help="path to write header to")
    parser.add_argument("--conf-out", required=True,
                        help="path to write configuration file to")

    return parser.parse_args()


def write_regs(dev):
    # Writes address/size output for the registers in dev's 'reg' property

    def reg_addr_name_alias(reg):
        return str2ident(reg.name) + "_BASE_ADDRESS" if reg.name else None

    def reg_size_name_alias(reg):
        return str2ident(reg.name) + "_SIZE" if reg.name else None

    for reg in dev.regs:
        out_dev(dev, reg_addr_ident(reg), hex(reg.addr),
                name_alias=reg_addr_name_alias(reg))

        if reg.size:
            out_dev(dev, reg_size_ident(reg), reg.size,
                    name_alias=reg_size_name_alias(reg))


def write_props(dev):
    # Writes any properties defined in the "properties" section of the binding
    # for the device

    for prop in dev.props.values():
        # Skip #size-cell and other property starting with #. Also skip mapping
        # properties like 'gpio-map'.
        if prop.name[0] == "#" or prop.name.endswith("-map"):
            continue

        # Skip properties that we handle elsewhere
        if prop.name in {"reg", "interrupts", "compatible", "interrupt-controller",
                "gpio-controller"}:
            continue

        if prop.description is not None:
            out_comment(prop.description, blank_before=False)

        ident = str2ident(prop.name)

        if isinstance(prop.val, bool):
            out_dev(dev, ident, 1 if prop.val else 0)
        elif isinstance(prop.val, str):
            out_dev_s(dev, ident, prop.val)
        elif isinstance(prop.val, int):
            out_dev(dev, ident, prop.val)
        elif isinstance(prop.val, list):
            for i, elm in enumerate(prop.val):
                out_fn = out_dev_s if isinstance(elm, str) else out_dev
                out_fn(dev, "{}_{}".format(ident, i), elm)
        elif isinstance(prop.val, bytes):
            out_dev(dev, ident,
                    "{ " + ", ".join("0x{:02x}".format(b) for b in prop.val) + " }")

        # Generate DT_..._ENUM if there's an 'enum:' key in the binding
        if prop.enum_index is not None:
            out_dev(dev, ident + "_ENUM", prop.enum_index)


def write_bus(dev):
    # Generate bus-related #defines

    if not dev.bus:
        return

    if dev.parent.label is None:
        err("missing 'label' property on {!r}".format(dev.parent))

    # #define DT_<DEV-IDENT>_BUS_NAME <BUS-LABEL>
    out_dev_s(dev, "BUS_NAME", str2ident(dev.parent.label))

    for compat in dev.compats:
        # #define DT_<COMPAT>_BUS_<BUS-TYPE> 1
        out("{}_BUS_{}".format(str2ident(compat), str2ident(dev.bus)), 1)


def write_existence_flags(dev):
    # Generate #defines of the form
    #
    #   #define DT_INST_<INSTANCE>_<COMPAT> 1
    #
    # These are flags for which devices exist.

    for compat in dev.compats:
        out("INST_{}_{}".format(dev.instance_no[compat], str2ident(compat)), 1)


def reg_addr_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for the address of
    # 'reg' in the output

    dev = reg.dev

    # NOTE: to maintain compat wit the old script we special case if there's
    # only a single register (we drop the '_0').
    if len(dev.regs) > 1:
        return "BASE_ADDRESS_{}".format(dev.regs.index(reg))
    else:
        return "BASE_ADDRESS"


def reg_size_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for the size of
    # 'reg' in the output

    dev = reg.dev

    # NOTE: to maintain compat wit the old script we special case if there's
    # only a single register (we drop the '_0').
    if len(dev.regs) > 1:
        return "SIZE_{}".format(dev.regs.index(reg))
    else:
        return "SIZE"


def dev_ident(dev):
    # Returns an identifier for the Device 'dev'. Used when building e.g. macro
    # names.

    # TODO: Handle PWM on STM
    # TODO: Better document the rules of how we generate things

    ident = ""

    if dev.bus:
        ident += "{}_{:X}_".format(
            str2ident(dev.parent.matching_compat), dev.parent.unit_addr)

    ident += "{}_".format(str2ident(dev.matching_compat))

    if dev.unit_addr is not None:
        ident += "{:X}".format(dev.unit_addr)
    elif dev.parent.unit_addr is not None:
        ident += "{:X}_{}".format(dev.parent.unit_addr, str2ident(dev.name))
    else:
        # This is a bit of a hack
        ident += "{}".format(str2ident(dev.name))

    return ident


def dev_aliases(dev):
    # Returns a list of aliases for the Device 'dev', used e.g. when building
    # macro names

    return dev_path_aliases(dev) + dev_instance_aliases(dev)


def dev_path_aliases(dev):
    # Returns a list of aliases for the Device 'dev', based on the aliases
    # registered for the device, in the /aliases node. Used when building e.g.
    # macro names.

    if dev.matching_compat is None:
        return []

    compat_s = str2ident(dev.matching_compat)

    aliases = []
    for alias in dev.aliases:
        aliases.append("ALIAS_{}".format(str2ident(alias)))
        # TODO: See if we can remove or deprecate this form
        aliases.append("{}_{}".format(compat_s, str2ident(alias)))

    return aliases


def dev_instance_aliases(dev):
    # Returns a list of aliases for the Device 'dev', based on the instance
    # number of the device (based on how many instances of that particular
    # device there are).
    #
    # This is a list since a device can have multiple 'compatible' strings,
    # each with their own instance number.

    return ["INST_{}_{}".format(dev.instance_no[compat], str2ident(compat))
            for compat in dev.compats]


def write_addr_size(edt, prop_name, prefix):
    # Writes <prefix>_BASE_ADDRESS and <prefix>_SIZE for the device
    # pointed at by the /chosen property named 'prop_name', if it exists

    dev = edt.chosen_dev(prop_name)
    if not dev:
        return

    if not dev.regs:
        err("missing 'reg' property in node pointed at by /chosen/{} ({!r})"
            .format(prop_name, dev))

    out_comment("/chosen/{} ({})".format(prop_name, dev.path))
    out("{}_BASE_ADDRESS".format(prefix), hex(dev.regs[0].addr))
    out("{}_SIZE".format(prefix), dev.regs[0].size//1024)


def write_flash(flash_dev):
    # Writes output for the node pointed at by the zephyr,flash property in
    # /chosen

    out_comment("/chosen/zephyr,flash ({})"
                .format(flash_dev.path if flash_dev else "missing"))

    if not flash_dev:
        # No flash device. Write dummy values.
        out("FLASH_BASE_ADDRESS", 0)
        out("FLASH_SIZE", 0)
        return

    if len(flash_dev.regs) != 1:
        err("expected zephyr,flash to have a single register, has {}"
            .format(len(flash_dev.regs)))

    if flash_dev.bus == "spi" and len(flash_dev.parent.regs) == 2:
        reg = flash_dev.parent.regs[1]  # QSPI flash
    else:
        reg = flash_dev.regs[0]

    out("FLASH_BASE_ADDRESS", hex(reg.addr))
    if reg.size:
        out("FLASH_SIZE", reg.size//1024)

    if "erase-block-size" in flash_dev.props:
        out("FLASH_ERASE_BLOCK_SIZE", flash_dev.props["erase-block-size"].val)

    if "write-block-size" in flash_dev.props:
        out("FLASH_WRITE_BLOCK_SIZE", flash_dev.props["write-block-size"].val)


def write_code_partition(code_dev):
    # Writes output for the node pointed at by the zephyr,code-partition
    # property in /chosen

    out_comment("/chosen/zephyr,code-partition ({})"
                .format(code_dev.path if code_dev else "missing"))

    if not code_dev:
        # No code partition. Write dummy values.
        out("CODE_PARTITION_OFFSET", 0)
        out("CODE_PARTITION_SIZE", 0)
        return

    if not code_dev.regs:
        err("missing 'regs' property on {!r}".format(code_dev))

    out("CODE_PARTITION_OFFSET", code_dev.regs[0].addr)
    out("CODE_PARTITION_SIZE", code_dev.regs[0].size)


def write_flash_partition(partition_dev, index):
    out_comment("Flash partition at " + partition_dev.path)

    if partition_dev.label is None:
        err("missing 'label' property on {!r}".format(partition_dev))

    # Generate label-based identifiers
    write_flash_partition_prefix(
        "FLASH_AREA_" + str2ident(partition_dev.label), partition_dev, index)

    # Generate index-based identifiers
    write_flash_partition_prefix(
        "FLASH_AREA_{}".format(index), partition_dev, index)


def write_flash_partition_prefix(prefix, partition_dev, index):
    # write_flash_partition() helper. Generates identifiers starting with
    # 'prefix'.

    out("{}_ID".format(prefix), index)

    out("{}_READ_ONLY".format(prefix), 1 if partition_dev.read_only else 0)

    for i, reg in enumerate(partition_dev.regs):
        # Also add aliases that point to the first sector (TODO: get rid of the
        # aliases?)
        out("{}_OFFSET_{}".format(prefix, i), reg.addr,
            aliases=["{}_OFFSET".format(prefix)] if i == 0 else [])
        out("{}_SIZE_{}".format(prefix, i), reg.size,
            aliases=["{}_SIZE".format(prefix)] if i == 0 else [])

    controller = partition_dev.flash_controller
    if controller.label is not None:
        out_s("{}_DEV".format(prefix), controller.label)


def write_required_label(ident, dev):
    # Helper function. Writes '#define <ident> "<label>"', where <label>
    # is the value of the 'label' property from 'dev'. Does nothing if
    # 'dev' is None.
    #
    # Errors out if 'dev' exists but has no label.

    if not dev:
        return

    if dev.label is None:
        err("missing 'label' property on {!r}".format(dev))

    out_s(ident, dev.label)


def write_irqs(dev):
    # Writes IRQ num and data for the interrupts in dev's 'interrupt' property

    def irq_name_alias(irq, cell_name):
        if not irq.name:
            return None

        alias = "IRQ_{}".format(str2ident(irq.name))
        if cell_name != "irq":
            alias += "_" + str2ident(cell_name)
        return alias

    for irq_i, irq in enumerate(dev.interrupts):
        # We ignore the controller for now
        for cell_name, cell_value in irq.specifier.items():
            ident = "IRQ_{}".format(irq_i)
            if cell_name != "irq":
                ident += "_" + str2ident(cell_name)

            out_dev(dev, ident, cell_value,
                    name_alias=irq_name_alias(irq, cell_name))


def write_gpios(dev):
    # Writes GPIO controller data for the gpios in dev's 'gpios' property

    for gpios in dev.gpios.values():
        for gpio_i, gpio in enumerate(gpios):
            write_gpio(dev, gpio, gpio_i if len(gpios) > 1 else None)


def write_gpio(dev, gpio, index=None):
    # Writes GPIO controller & data for the GPIO object 'gpio'. If 'index' is
    # not None, it is added as a suffix to identifiers.

    ctrl_ident = "GPIOS_CONTROLLER"
    if gpio.name:
        ctrl_ident = str2ident(gpio.name) + "_" + ctrl_ident
    if index is not None:
        ctrl_ident += "_{}".format(index)

    out_dev_s(dev, ctrl_ident, gpio.controller.label)

    for cell, val in gpio.specifier.items():
        cell_ident = "GPIOS_" + str2ident(cell)
        if gpio.name:
            cell_ident = str2ident(gpio.name) + "_" + cell_ident
        if index is not None:
            cell_ident += "_{}".format(index)

        out_dev(dev, cell_ident, val)


def write_spi_dev(dev):
    # Writes SPI device GPIO chip select data if there is any

    cs_gpio = edtlib.spi_dev_cs_gpio(dev)
    if cs_gpio is not None:
        write_gpio(dev, cs_gpio)


def write_pwms(dev):
    # Writes PWM controller and specifier info for the PWMs in dev's 'pwms'
    # property

    for pwm_i, pwm in enumerate(dev.pwms):
        write_pwm(dev, pwm, pwm_i if len(dev.pwms) > 1 else None)


def write_pwm(dev, pwm, index=None):
    # Writes PWM controller & data for the PWM object 'pwm'. If 'index' is
    # not None, it is added as a suffix to identifiers.

    if pwm.controller.label is not None:
        ctrl_ident = "PWMS_CONTROLLER"
        if index is not None:
            ctrl_ident += "_{}".format(index)
        out_dev_s(dev, ctrl_ident, pwm.controller.label)

    for cell, val in pwm.specifier.items():
        cell_ident = "PWMS_" + str2ident(cell)
        if index is not None:
            cell_ident += "_{}".format(index)

        out_dev(dev, cell_ident, val)


def write_iochannels(dev):
    # Writes IO channel controller and specifier info for the IO
    # channels in dev's 'io-channels' property

    for iochannel in dev.iochannels:
        if iochannel.controller.label is not None:
            out_dev_s(dev, "IO_CHANNELS_CONTROLLER", iochannel.controller.label)

        for spec, val in iochannel.specifier.items():
            out_dev(dev, "IO_CHANNELS_" + str2ident(spec), val)


def write_clocks(dev):
    # Writes clock controller and specifier info for the clock in dev's 'clock'
    # property

    for clock_i, clock in enumerate(dev.clocks):
        if clock.controller.label is not None:
            out_dev_s(dev, "CLOCK_CONTROLLER", clock.controller.label)

        if clock.frequency is not None:
            out_dev(dev, "CLOCKS_CLOCK_FREQUENCY", clock.frequency)

        for spec, val in clock.specifier.items():
            if clock_i == 0:
                clk_name_alias = "CLOCK_" + str2ident(spec)
            else:
                clk_name_alias = None

            out_dev(dev, "CLOCK_{}_{}".format(str2ident(spec), clock_i), val,
                    name_alias=clk_name_alias)


def str2ident(s):
    # Converts 's' to a form suitable for (part of) an identifier

    return s.replace("-", "_") \
            .replace(",", "_") \
            .replace("@", "_") \
            .replace("/", "_") \
            .replace(".", "_") \
            .replace("+", "PLUS") \
            .upper()


def out_dev(dev, ident, val, name_alias=None):
    # Writes an
    #
    #   <device prefix>_<ident> = <val>
    #
    # assignment, along with a set of
    #
    #   <device alias>_<ident>
    #
    # aliases, for each device alias. If 'name_alias' (a string) is passed,
    # then these additional aliases are generated:
    #
    #   <device prefix>_<name alias>
    #   <device alias>_<name alias> (for each device alias)
    #
    # 'name_alias' is used for reg-names and the like.

    dev_prefix = dev_ident(dev)

    aliases = [alias + "_" + ident for alias in dev_aliases(dev)]
    if name_alias is not None:
        aliases.append(dev_prefix + "_" + name_alias)
        aliases += [alias + "_" + name_alias for alias in dev_aliases(dev)]

    out(dev_prefix + "_" + ident, val, aliases)


def out_dev_s(dev, ident, s):
    # Like out_dev(), but puts quotes around 's' and escapes any double quotes
    # and backslashes within it

    # \ must be escaped before " to avoid double escaping
    out_dev(dev, ident, '"{}"'.format(escape(s)))


def out_s(ident, val):
    # Like out(), but puts quotes around 's' and escapes any double quotes and
    # backslashes within it

    out(ident, '"{}"'.format(escape(val)))


def out(ident, val, aliases=()):
    # Writes '#define <ident> <val>' to the header and '<ident>=<val>' to the
    # the configuration file.
    #
    # Also writes any aliases listed in 'aliases' (an iterable). For the
    # header, these look like '#define <alias> <ident>'. For the configuration
    # file, the value is just repeated as '<alias>=<val>' for each alias.

    print("#define DT_{:40} {}".format(ident, val), file=header_file)
    print("DT_{}={}".format(ident, val), file=conf_file)

    for alias in aliases:
        if alias != ident:
            print("#define DT_{:40} DT_{}".format(alias, ident),
                  file=header_file)
            # For the configuration file, the value is just repeated for all
            # the aliases
            print("DT_{}={}".format(alias, val), file=conf_file)


def out_comment(s, blank_before=True):
    # Writes 's' as a comment to the header and configuration file. 's' is
    # allowed to have multiple lines. blank_before=True adds a blank line
    # before the comment.

    if blank_before:
        print(file=header_file)
        print(file=conf_file)

    # Double-space in header for readability
    print("/*  " + s + "  */", file=header_file)
    print("\n".join("# " + line for line in s.splitlines()), file=conf_file)


def escape(s):
    # Backslash-escapes any double quotes and backslashes in 's'

    # \ must be escaped before " to avoid double escaping
    return s.replace("\\", "\\\\").replace('"', '\\"')


def err(s):
    raise Exception(s)


if __name__ == "__main__":
    main()
