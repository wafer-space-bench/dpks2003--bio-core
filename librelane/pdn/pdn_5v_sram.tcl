# BIO-lite SRAM PDN configuration
# 8 instances, all N orientation, no brackets in names

# Core 0 SRAMs
foreach idx {0 1 2 3} {
    set inst_name "i_chip_core.u_bio_lite.u_bio.u_imem_core0.sram_byte${idx}"

    define_pdn_grid \
        -macro \
        -instances $inst_name \
        -name "sram_c0_b${idx}" \
        -starts_with POWER \
        -halo "$::env(PDN_HORIZONTAL_HALO) $::env(PDN_VERTICAL_HALO)"

    add_pdn_connect \
        -grid "sram_c0_b${idx}" \
        -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"

    add_pdn_connect \
        -grid "sram_c0_b${idx}" \
        -layers "$::env(PDN_VERTICAL_LAYER) Metal3"

    add_pdn_stripe \
        -grid "sram_c0_b${idx}" \
        -layer Metal4 \
        -width 2.36 \
        -offset 1.18 \
        -spacing 0.28 \
        -pitch 426.86 \
        -starts_with GROUND \
        -number_of_straps 2

    add_pdn_stripe \
        -grid "sram_c0_b${idx}" \
        -layer Metal4 \
        -width 4.00 \
        -offset 65.93 \
        -spacing 0.28 \
        -pitch 50 \
        -starts_with GROUND \
        -number_of_straps 7
}

# Core 1 SRAMs
foreach idx {0 1 2 3} {
    set inst_name "i_chip_core.u_bio_lite.u_bio.u_imem_core1.sram_byte${idx}"

    define_pdn_grid \
        -macro \
        -instances $inst_name \
        -name "sram_c1_b${idx}" \
        -starts_with POWER \
        -halo "$::env(PDN_HORIZONTAL_HALO) $::env(PDN_VERTICAL_HALO)"

    add_pdn_connect \
        -grid "sram_c1_b${idx}" \
        -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"

    add_pdn_connect \
        -grid "sram_c1_b${idx}" \
        -layers "$::env(PDN_VERTICAL_LAYER) Metal3"

    add_pdn_stripe \
        -grid "sram_c1_b${idx}" \
        -layer Metal4 \
        -width 2.36 \
        -offset 1.18 \
        -spacing 0.28 \
        -pitch 426.86 \
        -starts_with GROUND \
        -number_of_straps 2

    add_pdn_stripe \
        -grid "sram_c1_b${idx}" \
        -layer Metal4 \
        -width 4.00 \
        -offset 65.93 \
        -spacing 0.28 \
        -pitch 50 \
        -starts_with GROUND \
        -number_of_straps 7
}