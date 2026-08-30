#!/usr/bin/env python3
"""Build /tmp/tb_probe4.v: probe3 + a stuck-state watchdog."""
s = open("/tmp/tb_probe3.v").read()
watchdog = """
// ---------------- watchdog: photograph the stuck state ----------------
reg [31:0] last_egress_cyc = 0;
integer wd_k;
always @(posedge clk) if (c_stx_val) last_egress_cyc <= cyc;
always @(posedge clk) begin
    if (cyc > 200000 && (cyc - last_egress_cyc) > 4000000) begin
        $display("WATCHDOG cyc=%0d: no egress for 4M cycles", cyc);
        $display("  c_state=%0d c_err=%0d c_srdy=%b", c_state, c_err, c_srdy);
        $display("  boot_state=%0d run=%b", dut_cold.boot_state, dut_cold.run);
        $display("  core states: n0=%0d n1=%0d n2=%0d n5=%0d n6=%0d n7=%0d n15io=NA",
            dut_cold.nodes[0].conn0.u_cell.u_core.state,
            dut_cold.nodes[1].connc.u_cell.u_core.state,
            dut_cold.nodes[2].connc.u_cell.u_core.state,
            dut_cold.nodes[5].connc.u_cell.u_core.state,
            dut_cold.nodes[6].connc.u_cell.u_core.state,
            dut_cold.nodes[7].connc.u_cell.u_core.state);
        $finish;
    end
end
"""
marker = "    // ---------------- script player ------------------------------------"
assert marker in s
s = s.replace(marker, watchdog + marker)
open("/tmp/tb_probe4.v", "w").write(s)
print("probe4 written")
