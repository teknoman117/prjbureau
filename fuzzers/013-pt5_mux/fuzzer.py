from util import database, toolchain, bitdiff, progress


with database.transact() as db:
    for device_name, device in db.items():
        if device_name.startswith('ATF1508'):
            progress()
            print(f"Skipping {device_name} because the fuzzer is broken on it")
            continue
        else:
            progress(device_name)

        package, pinout = next(iter(device['pins'].items()))
        for macrocell_name, macrocell in device['macrocells'].items():
            if macrocell['pad'] not in pinout:
                progress()
                print(f"Skipping {macrocell_name} on {device_name} because it is not bonded out")
                continue
            else:
                progress(1)

            for other_macrocell_name, other_macrocell in device['macrocells'].items():
                if other_macrocell_name != macrocell_name and other_macrocell['pad'] in pinout:
                    break
            else:
                assert False

            def run(code):
                return toolchain.run(
                    f"module top(input C1, C2, E1, R, I, output Q); "
                    f"{code} "
                    f"endmodule",
                    {
                        'C1': pinout['C1'],
                        'C2': pinout['C2'],
                        'E1': pinout['E1'],
                        'R': pinout['R'],
                        'I': pinout[other_macrocell['pad']],
                        'Q': pinout[macrocell['pad']],
                    },
                    f"{device_name}-{package}",
                    strategy={'xor_synthesis': 'on'})

            if not toolchain.has_aprim:
                # TT2 input cannot express the XOR gate used below, but it is fitted as written, so
                # a direct netlist works: the clock and the reset use PT4 and PT3, leaving PT1, PT2
                # and PT5 for the sum, and the same product is either a sum term or the async set.
                # The fitter may allocate the sum terms in another order, hence the scope.
                f_sum = run(
                    f"wire Y1, Y2, Y3, Y4, D; "
                    f"AND2 a1(C1, C2, Y1); "
                    f"AND2 a2(C2, E1, Y2); "
                    f"AND2 a3(E1, I, Y3); "
                    f"AND2 a4(I, R, Y4); "
                    f"OR3 o1(Y2, Y3, Y4, D); "
                    f"DFFAR d(Y1, Y1, D, Q); "
                )
                f_as = run(
                    f"wire Y1, Y2, Y3, Y4, D; "
                    f"AND2 a1(C1, C2, Y1); "
                    f"AND2 a2(C2, E1, Y2); "
                    f"AND2 a3(E1, I, Y3); "
                    f"AND2 a4(I, R, Y4); "
                    f"OR2 o1(Y2, Y3, D); "
                    f"DFFARS d(Y1, Y1, Y4, D, Q); "
                )
                macrocell.update({
                    'pt5_mux':
                        bitdiff.describe(1, {'as_oe': f_as, 'sum': f_sum},
                                         scope=range(*device['ranges']['macrocells'])),
                })
                continue

            # Took me a long time to find a netlist this pathological.
            f_sum = run(
                f"wire Y1, Y2, Y3, Y4, Y5; "
                f"AND2 a1(C1, C2, Y1); "
                f"AND2 a2(I, R, Y2); "
                f"OR2 o1(Y2, E1, Y3); "
                f"XOR2 x1(Y1, Y3, Y5); "
                f"DFFAR d(Y1, Y1, Y5, Q); "
            )
            f_as = run(
                f"wire Y1, Y2, Y3, Y4, Y5; "
                f"AND2 a1(C1, C2, Y1); "
                f"AND2 a2(I, R, Y2); "
                f"XOR2 x1(Y1, Y2, Y5); "
                f"DFFARS d(Y1, Y1, E1, Y5, Q); "
            )

            # PT5 can be either a part of the sum term, or serve as async set/output enable.
            macrocell.update({
                'pt5_mux':
                    bitdiff.describe(1, {'as_oe': f_as, 'sum': f_sum}),
            })
