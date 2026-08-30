// tb_deck_cosim.v -- QUILT-DECK FPGA backend: the back-deck day on the real
// serialized fabric RTL (quilt-verilog rtl/q_serfabric_top.v), driven from
// host-side framed scripts, differential against the python soft model.
//
// Two DUTs, one file set (all under cosim/run/):
//   DUT-COLD (SER_BOOT_QUF=0, gate mode, TPW0=15): released with the
//     commissioning word 0x51 0x46, then plays run/cold.ops -- the full
//     fishing day from a cold fabric (bind/link commissioning, landings,
//     moves, hooks, ticks, night). End state dumped to run/cold.dump.
//   DUT-WARM (SER_BOOT_QUF=1): boots the day's saved QUF (boot image hex,
//     run/boot.hex + i_eod), then plays run/warm.ops -- the SAME day's
//     training stream replayed (the §6 doctrine: warm dials + topology,
//     ladders earned back). End state dumped to run/warm.dump.
//
// Script format (one op per line, $fscanf-friendly):
//   1 <20-hex-digit 80-bit frame>   send the 10-byte flit frame
//   2 <n>                           settle n cycles
//   3 <n>                           run n ticks (count posedges of tick)
//   9                               dump full fabric state
//   0                               end of script
// Egress (flits delivered to the host) logged as "E <20-hex>" lines in
// file order; state dump lines in run/*.dump:
//   DC <cell> <16 x dial>
//   DA <cell> <act> <refr> <ftrace>
//   DE <cell> <slot> <valid> <peer> <base> <hl_cnt> <wh> <age> <8 buckets>
//
// The python driver (deck/cosim.py) builds scripts from the same day log,
// predicts the egress stream with the soft model, runs this TB, and asserts
// frame-exact egress + byte-identical final QUF. Any divergence is a bug in
// one of the three engines -- the differential TB is the referee.
//
// Run: iverilog -g2005 -o run/tb.vvp $QV/rtl/*.v tb_deck_cosim.v && vvp
// (deck/cosim.py does exactly this; quilt-verilog is read-only).
`timescale 1ns/1ps
module tb_deck_cosim;
    reg clk = 0;
    always #5 clk = ~clk;

    integer errors = 0;

    // ---------------- DUT-COLD: gate mode, cold commissioning ----------
    reg         cpor = 0;
    reg         c_sval = 0, c_eod = 0;
    reg  [7:0]  c_sbyte = 0;
    wire        c_srdy, c_stx_val;
    wire [7:0]  c_stx;
    wire        c_boot_ok, c_epoch, c_ovf;
    wire [2:0]  c_state;
    wire [7:0]  c_err;

    q_serfabric_top #(.NCELL(15), .SER_BOOT_QUF(0), .TPW0(15)) dut_cold (
        .clk(clk), .rst_n(cpor),
        .i_sval(c_sval), .o_srdy(c_srdy), .i_sbyte(c_sbyte), .i_eod(c_eod),
        .o_stx_val(c_stx_val), .i_strdy(1'b1), .o_stx(c_stx),
        .o_boot_ok(c_boot_ok), .o_epoch(c_epoch),
        .o_state(c_state), .o_err(c_err), .o_ovf(c_ovf)
    );

    // ---------------- DUT-WARM: QUF boot + replay ----------------------
    reg         wpor = 0;
    reg         w_sval = 0, w_eod = 0;
    reg  [7:0]  w_sbyte = 0;
    wire        w_srdy, w_stx_val;
    wire [7:0]  w_stx;
    wire        w_boot_ok, w_epoch, w_ovf;
    wire [2:0]  w_state;
    wire [7:0]  w_err;

    q_serfabric_top #(.NCELL(15), .SER_BOOT_QUF(1)) dut_warm (
        .clk(clk), .rst_n(wpor),
        .i_sval(w_sval), .o_srdy(w_srdy), .i_sbyte(w_sbyte), .i_eod(w_eod),
        .o_stx_val(w_stx_val), .i_strdy(1'b1), .o_stx(w_stx),
        .o_boot_ok(w_boot_ok), .o_epoch(w_epoch),
        .o_state(w_state), .o_err(w_err), .o_ovf(w_ovf)
    );

    integer efc, efw;
    integer cyc = 0;
    always @(posedge clk) cyc <= cyc + 1;

    // ---------------- byte senders (handshake-honest) ------------------
    // negedge-driven byte lane (house rule from tb_serfabric: driving on
    // posedge races the DUT's sampling -- the release word lands wrong,
    // err 11; values change on negedge, sampled on posedge, no race)
    task send_cold(input [7:0] b);
        begin
            @(negedge clk);
            while (!c_srdy) @(negedge clk);
            c_sval = 1; c_sbyte = b;
            @(negedge clk);
            c_sval = 0;
        end
    endtask
    task send_warm(input [7:0] b);
        begin
            @(negedge clk);
            while (!w_srdy) @(negedge clk);
            w_sval = 1; w_sbyte = b;
            @(negedge clk);
            w_sval = 0;
        end
    endtask

    task send_frame_cold(input [79:0] w);
        integer k;
        begin
            for (k = 9; k >= 0; k = k - 1)
                send_cold(w[8*k +: 8]);
        end
    endtask
    task send_frame_warm(input [79:0] w);
        integer k;
        begin
            for (k = 9; k >= 0; k = k - 1)
                send_warm(w[8*k +: 8]);
        end
    endtask

    task settle(input integer n);
        integer i;
        begin
            for (i = 0; i < n; i = i + 1) @(posedge clk);
        end
    endtask

    task run_ticks_cold(input integer n);
        integer i;
        begin
            for (i = 0; i < n; i = i + 1) @(posedge dut_cold.tick);
            settle(4096);   // fire fanout drain
        end
    endtask
    task run_ticks_warm(input integer n);
        integer i;
        begin
            for (i = 0; i < n; i = i + 1) @(posedge dut_warm.tick);
            settle(4096);
        end
    endtask

    // ---------------- egress capture (both lanes, always on) -----------
    // frame assembly from the egress serializer: count bytes (the tbusy
    // edge lags the first byte by the NBA -- byte counting cannot miss it)
    reg [3:0]  c_tby = 0;
    reg [79:0] c_tcap = 0;
    always @(posedge clk) begin
        if (c_stx_val) begin
            if (c_tby == 9) begin
                $fdisplay(efc, "E %h", {c_tcap[71:0], c_stx});
                c_tby <= 0;
            end else begin
                c_tcap <= {c_tcap[71:0], c_stx};
                c_tby <= c_tby + 1;
            end
        end
    end
    reg [3:0]  w_tby = 0;
    reg [79:0] w_tcap = 0;
    always @(posedge clk) begin
        if (w_stx_val) begin
            if (w_tby == 9) begin
                $fdisplay(efw, "E %h", {w_tcap[71:0], w_stx});
                w_tby <= 0;
            end else begin
                w_tcap <= {w_tcap[71:0], w_stx};
                w_tby <= w_tby + 1;
            end
        end
    end

    // ---------------- state dump (generate blocks: iverilog needs
    // constant hierarchical indices; one strobe line per cell, pulsed
    // sequentially by the dump tasks below) ---------------------------
    integer df_c, df_w;
    reg [14:0] dump_c_strobe, dump_w_strobe;

    genvar dg, dk;
    generate
    for (dg = 0; dg < 15; dg = dg + 1) begin : cd
        for (dk = 0; dk < 4; dk = dk + 1) begin : cdk
            if (dg == 0) begin : c0
                always @(posedge dump_c_strobe[dg]) begin
                    if (dk == 0) begin
                        $fdisplay(df_c, "DC %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", dg,
                            dut_cold.nodes[0].conn0.u_cell.u_df.dial[0], dut_cold.nodes[0].conn0.u_cell.u_df.dial[1],
                            dut_cold.nodes[0].conn0.u_cell.u_df.dial[2], dut_cold.nodes[0].conn0.u_cell.u_df.dial[3],
                            dut_cold.nodes[0].conn0.u_cell.u_df.dial[4], dut_cold.nodes[0].conn0.u_cell.u_df.dial[5],
                            dut_cold.nodes[0].conn0.u_cell.u_df.dial[6], dut_cold.nodes[0].conn0.u_cell.u_df.dial[7],
                            dut_cold.nodes[0].conn0.u_cell.u_df.dial[8], dut_cold.nodes[0].conn0.u_cell.u_df.dial[9],
                            dut_cold.nodes[0].conn0.u_cell.u_df.dial[10], dut_cold.nodes[0].conn0.u_cell.u_df.dial[11],
                            dut_cold.nodes[0].conn0.u_cell.u_df.dial[12], dut_cold.nodes[0].conn0.u_cell.u_df.dial[13],
                            dut_cold.nodes[0].conn0.u_cell.u_df.dial[14], dut_cold.nodes[0].conn0.u_cell.u_df.dial[15]);
                        $fdisplay(df_c, "DA %0d %0d %0d %0d", dg,
                            dut_cold.nodes[0].conn0.u_cell.u_core.act,
                            dut_cold.nodes[0].conn0.u_cell.u_core.refr,
                            dut_cold.nodes[0].conn0.u_cell.u_core.u_eg.f);
                    end
                    $fdisplay(df_c, "DE %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", dg, dk,
                        dut_cold.nodes[0].conn0.u_cell.u_core.ev[dk],
                        dut_cold.nodes[0].conn0.u_cell.u_core.etab[dk],
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.base,
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.hl_cnt,
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.wh,
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.age,
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[0],
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[1],
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[2],
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[3],
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[4],
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[5],
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[6],
                        dut_cold.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[7]);
                    if (dk == 0) begin
                        $fdisplay(df_w, "DC %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", dg,
                            dut_warm.nodes[0].conn0.u_cell.u_df.dial[0], dut_warm.nodes[0].conn0.u_cell.u_df.dial[1],
                            dut_warm.nodes[0].conn0.u_cell.u_df.dial[2], dut_warm.nodes[0].conn0.u_cell.u_df.dial[3],
                            dut_warm.nodes[0].conn0.u_cell.u_df.dial[4], dut_warm.nodes[0].conn0.u_cell.u_df.dial[5],
                            dut_warm.nodes[0].conn0.u_cell.u_df.dial[6], dut_warm.nodes[0].conn0.u_cell.u_df.dial[7],
                            dut_warm.nodes[0].conn0.u_cell.u_df.dial[8], dut_warm.nodes[0].conn0.u_cell.u_df.dial[9],
                            dut_warm.nodes[0].conn0.u_cell.u_df.dial[10], dut_warm.nodes[0].conn0.u_cell.u_df.dial[11],
                            dut_warm.nodes[0].conn0.u_cell.u_df.dial[12], dut_warm.nodes[0].conn0.u_cell.u_df.dial[13],
                            dut_warm.nodes[0].conn0.u_cell.u_df.dial[14], dut_warm.nodes[0].conn0.u_cell.u_df.dial[15]);
                        $fdisplay(df_w, "DA %0d %0d %0d %0d", dg,
                            dut_warm.nodes[0].conn0.u_cell.u_core.act,
                            dut_warm.nodes[0].conn0.u_cell.u_core.refr,
                            dut_warm.nodes[0].conn0.u_cell.u_core.u_eg.f);
                    end
                    $fdisplay(df_w, "DE %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", dg, dk,
                        dut_warm.nodes[0].conn0.u_cell.u_core.ev[dk],
                        dut_warm.nodes[0].conn0.u_cell.u_core.etab[dk],
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.base,
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.hl_cnt,
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.wh,
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.age,
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[0],
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[1],
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[2],
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[3],
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[4],
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[5],
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[6],
                        dut_warm.nodes[0].conn0.u_cell.edges[dk].u_hebb.c[7]);
                end
            end else begin : cn
                always @(posedge dump_c_strobe[dg]) begin
                    if (dk == 0) begin
                        $fdisplay(df_c, "DC %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", dg,
                            dut_cold.nodes[dg].connc.u_cell.u_df.dial[0], dut_cold.nodes[dg].connc.u_cell.u_df.dial[1],
                            dut_cold.nodes[dg].connc.u_cell.u_df.dial[2], dut_cold.nodes[dg].connc.u_cell.u_df.dial[3],
                            dut_cold.nodes[dg].connc.u_cell.u_df.dial[4], dut_cold.nodes[dg].connc.u_cell.u_df.dial[5],
                            dut_cold.nodes[dg].connc.u_cell.u_df.dial[6], dut_cold.nodes[dg].connc.u_cell.u_df.dial[7],
                            dut_cold.nodes[dg].connc.u_cell.u_df.dial[8], dut_cold.nodes[dg].connc.u_cell.u_df.dial[9],
                            dut_cold.nodes[dg].connc.u_cell.u_df.dial[10], dut_cold.nodes[dg].connc.u_cell.u_df.dial[11],
                            dut_cold.nodes[dg].connc.u_cell.u_df.dial[12], dut_cold.nodes[dg].connc.u_cell.u_df.dial[13],
                            dut_cold.nodes[dg].connc.u_cell.u_df.dial[14], dut_cold.nodes[dg].connc.u_cell.u_df.dial[15]);
                        $fdisplay(df_c, "DA %0d %0d %0d %0d", dg,
                            dut_cold.nodes[dg].connc.u_cell.u_core.act,
                            dut_cold.nodes[dg].connc.u_cell.u_core.refr,
                            dut_cold.nodes[dg].connc.u_cell.u_core.u_eg.f);
                    end
                    $fdisplay(df_c, "DE %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", dg, dk,
                        dut_cold.nodes[dg].connc.u_cell.u_core.ev[dk],
                        dut_cold.nodes[dg].connc.u_cell.u_core.etab[dk],
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.base,
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.hl_cnt,
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.wh,
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.age,
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[0],
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[1],
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[2],
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[3],
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[4],
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[5],
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[6],
                        dut_cold.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[7]);
                    if (dk == 0) begin
                        $fdisplay(df_w, "DC %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", dg,
                            dut_warm.nodes[dg].connc.u_cell.u_df.dial[0], dut_warm.nodes[dg].connc.u_cell.u_df.dial[1],
                            dut_warm.nodes[dg].connc.u_cell.u_df.dial[2], dut_warm.nodes[dg].connc.u_cell.u_df.dial[3],
                            dut_warm.nodes[dg].connc.u_cell.u_df.dial[4], dut_warm.nodes[dg].connc.u_cell.u_df.dial[5],
                            dut_warm.nodes[dg].connc.u_cell.u_df.dial[6], dut_warm.nodes[dg].connc.u_cell.u_df.dial[7],
                            dut_warm.nodes[dg].connc.u_cell.u_df.dial[8], dut_warm.nodes[dg].connc.u_cell.u_df.dial[9],
                            dut_warm.nodes[dg].connc.u_cell.u_df.dial[10], dut_warm.nodes[dg].connc.u_cell.u_df.dial[11],
                            dut_warm.nodes[dg].connc.u_cell.u_df.dial[12], dut_warm.nodes[dg].connc.u_cell.u_df.dial[13],
                            dut_warm.nodes[dg].connc.u_cell.u_df.dial[14], dut_warm.nodes[dg].connc.u_cell.u_df.dial[15]);
                        $fdisplay(df_w, "DA %0d %0d %0d %0d", dg,
                            dut_warm.nodes[dg].connc.u_cell.u_core.act,
                            dut_warm.nodes[dg].connc.u_cell.u_core.refr,
                            dut_warm.nodes[dg].connc.u_cell.u_core.u_eg.f);
                    end
                    $fdisplay(df_w, "DE %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", dg, dk,
                        dut_warm.nodes[dg].connc.u_cell.u_core.ev[dk],
                        dut_warm.nodes[dg].connc.u_cell.u_core.etab[dk],
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.base,
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.hl_cnt,
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.wh,
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.age,
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[0],
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[1],
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[2],
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[3],
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[4],
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[5],
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[6],
                        dut_warm.nodes[dg].connc.u_cell.edges[dk].u_hebb.c[7]);
                end
            end
        end
    end
    endgenerate

    task dump_all(input integer fdc, input integer fdw);
        integer g;
        begin
            df_c = fdc; df_w = fdw;
            for (g = 0; g < 15; g = g + 1) begin
                dump_c_strobe[g] <= 1;
                @(posedge clk);
                dump_c_strobe[g] <= 0;
                @(posedge clk);
            end
        end
    endtask

    // ---------------- script player ------------------------------------
    task play_cold(input integer fd);
        integer code, n, r;
        reg [79:0] w;
        begin
            code = -1;
            while (code != 0) begin
                r = $fscanf(fd, "%d", code);
                if (r != 1) code = 0;
                if (code == 1) begin
                    r = $fscanf(fd, "%h\n", w);
                    send_frame_cold(w);
                end else if (code == 2) begin
                    r = $fscanf(fd, "%d\n", n);
                    settle(n);
                end else if (code == 3) begin
                    r = $fscanf(fd, "%d\n", n);
                    run_ticks_cold(n);
                end else if (code == 9) begin
                    settle(64);
                end
            end
        end
    endtask

    task play_warm(input integer fd);
        integer code, n, r;
        reg [79:0] w;
        begin
            code = -1;
            while (code != 0) begin
                r = $fscanf(fd, "%d", code);
                if (r != 1) code = 0;
                if (code == 1) begin
                    r = $fscanf(fd, "%h\n", w);
                    send_frame_warm(w);
                end else if (code == 2) begin
                    r = $fscanf(fd, "%d\n", n);
                    settle(n);
                end else if (code == 3) begin
                    r = $fscanf(fd, "%d\n", n);
                    run_ticks_warm(n);
                end else if (code == 9) begin
                    settle(64);
                end
            end
        end
    endtask

    // ---------------- the main sequence --------------------------------
    integer sfd, bfd, dfd, dfw2, r;
    reg [7:0] bhex;
    initial begin
        efc = $fopen("run/cold.egr", "w");
        efw = $fopen("run/warm.egr", "w");
        if (efc == 0 || efw == 0) begin
            $display("FAIL: cannot open egress logs under run/");
            $finish;
        end

        // POR both
        cpor = 0; wpor = 0;
        settle(10);
        cpor = 1; wpor = 1;
        settle(10);

        // ---- phase 1: the cold day on DUT-COLD (gate release) ----
        send_cold(8'h51); send_cold(8'h46);          // commissioning word
        settle(64);
        if (c_state != 3'd5) begin
            $display("FAIL: DUT-COLD did not release (state %0d err %0d)", c_state, c_err);
            errors = errors + 1;
        end
        sfd = $fopen("run/cold.ops", "r");
        if (sfd == 0) begin $display("FAIL: no run/cold.ops"); $finish; end
        play_cold(sfd);
        $fclose(sfd);
        settle(256);

        // ---- phase 2: warm boot + replay on DUT-WARM ----
        bfd = $fopen("run/boot.hex", "r");
        if (bfd == 0) begin $display("FAIL: no run/boot.hex"); $finish; end
        r = 1;
        while (r == 1) begin
            r = $fscanf(bfd, "%h\n", bhex);
            if (r == 1) send_warm(bhex);
        end
        $fclose(bfd);
        // wait for the loader to finish the container BEFORE eod (S_LOAD
        // exits on ld_done; eod while still parsing = E_TRUNC)
        while (w_state == 3'd1 || w_state == 3'd2) @(posedge clk);
        w_eod = 1; @(negedge clk); w_eod = 0;   // transport end-of-stream
        settle(256);
        if (w_state != 3'd5) begin
            $display("FAIL: DUT-WARM boot failed (state %0d err %0d)", w_state, w_err);
            errors = errors + 1;
        end
        sfd = $fopen("run/warm.ops", "r");
        if (sfd == 0) begin $display("FAIL: no run/warm.ops"); $finish; end
        play_warm(sfd);
        $fclose(sfd);
        settle(256);

        // both DUTs final: one dump pass writes cold.dump + warm.dump
        dfd = $fopen("run/cold.dump", "w");
        dfw2 = $fopen("run/warm.dump", "w");
        dump_all(dfd, dfw2);
        $fclose(dfd);
        $fclose(dfw2);

        $fclose(efc); $fclose(efw);
        if (errors == 0) $display("COSIM DONE (egress+dump written)");
        else $display("COSIM DONE with %0d errors", errors);
        $finish;
    end
endmodule
